import logging
import os
import shutil
import sys
import time
import uuid
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from threading import Thread
from typing import Any, Callable, Generator, List, Optional

import schedule
import streamlit as st
import yaml

current_dir = os.path.dirname(os.path.abspath(__file__))
kit_dir = os.path.abspath(os.path.join(current_dir, '..'))
repo_dir = os.path.abspath(os.path.join(kit_dir, '..'))

sys.path.append(kit_dir)
sys.path.append(repo_dir)

from function_calling.src.function_calling import FunctionCallingLlm
from function_calling.src.tools import calculator, get_time, python_repl, query_db, rag, translate
from utils.visual.env_utils import are_credentials_set, env_input_fields, initialize_env_variables, save_credentials

logging.basicConfig(level=logging.INFO)

CONFIG_PATH = os.path.join(kit_dir, 'config.yaml')
PRESET_QUERIES_PATH = os.path.join(kit_dir, 'prompts', 'streamlit_preset_queries.yaml')

# tool mapping of defined tools
TOOLS = {
    'get_time': get_time,
    'calculator': calculator,
    'python_repl': python_repl,
    'query_db': query_db,
    'translate': translate,
    'rag': rag,
}
EXIT_TIME_DELTA = 30


def load_config() -> Any:
    with open(CONFIG_PATH, 'r') as yaml_file:
        return yaml.safe_load(yaml_file)


def load_preset_queries() -> Any:
    with open(PRESET_QUERIES_PATH, 'r') as yaml_file:
        return yaml.safe_load(yaml_file)


config = load_config()
prod_mode = config.get('prod_mode', False)
st_tools = config.get('st_tools', {})
st_preset_queries = load_preset_queries()

db_path = config['tools']['query_db']['db'].get('path')
additional_env_vars = config.get('additional_env_vars', None)


@contextmanager
def st_capture(output_func: Callable[[str], None]) -> Generator[str, None, None]:
    """
    context manager to catch stdout and send it to an output streamlit element

    Args:
        output_func (function to write terminal output in

    Yields:
        Generator:
    """
    with StringIO() as stdout, redirect_stdout(stdout):
        old_write = stdout.write

        def new_write(string: str) -> int:
            ret = old_write(string)
            output_func(stdout.getvalue())
            return ret

        stdout.write = new_write  # type: ignore
        yield  # type: ignore


def delete_temp_dir(temp_dir: str) -> None:
    """
    Delete the temporary directory and its contents.

    Args:
        temp_dir (str): The path of the temporary directory.
    """

    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            logging.info(f'Temporary directory {temp_dir} deleted.')
        except:
            logging.info(f'Could not delete temporary directory {temp_dir}.')


def schedule_temp_dir_deletion(temp_dir: str, delay_minutes: int) -> None:
    """
    Schedule the deletion of the temporary directory after a delay.

    Args:
        temp_dir (str): The path of the temporary directory.
        delay_minutes (int): The delay in minutes after which the temporary directory should be deleted.
    """

    schedule.every(delay_minutes).minutes.do(delete_temp_dir, temp_dir).tag(temp_dir)

    def run_scheduler() -> None:
        while schedule.get_jobs(temp_dir):
            schedule.run_pending()
            time.sleep(1)

    # Run scheduler in a separate thread to be non-blocking
    Thread(target=run_scheduler, daemon=True).start()


def create_temp_db(out_path: str) -> None:
    """
    Create a temporary database at the specified path.

    Args:
        out_path (str): The path where the temporary database will be created.
    """
    logging.info(f'creating temp db in {out_path}')
    directory = os.path.dirname(out_path)
    if not os.path.exists(directory):
        os.makedirs(directory)
    shutil.copy2(os.path.join(kit_dir, db_path), out_path)


def set_fc_llm(tools: List[Any]) -> None:
    """
    Set the FunctionCallingLlm object with the selected tools

    Args:
        tools (list): list of tools to be used
    """
    set_tools = [TOOLS[name] for name in tools]
    if query_db in set_tools:
        if prod_mode:
            create_temp_db(st.session_state.session_temp_db)
            schedule_temp_dir_deletion(os.path.dirname(st.session_state.session_temp_db), EXIT_TIME_DELTA)
            st.toast("""your session will be active for the next 30 minutes, after this time tmp db will be deleted""")

    st.session_state.fc = FunctionCallingLlm(set_tools)


def handle_userinput(user_question: Optional[str]) -> None:
    """
    Handle user input and generate a response, also update chat UI in streamlit app

    Args:
        user_question (str): The user's question or input.
    """
    global output
    if user_question:
        with st.spinner('Processing...'):
            with st_capture(output.code):  # type: ignore
                response = st.session_state.fc.function_call_llm(
                    query=user_question, max_it=st.session_state.max_iterations, debug=True
                )

        st.session_state.chat_history.append(user_question)
        st.session_state.chat_history.append(response)

    for ques, ans in zip(
        st.session_state.chat_history[::2],
        st.session_state.chat_history[1::2],
    ):
        with st.chat_message('user'):
            st.write(f'{ques}')

        with st.chat_message(
            'ai',
            avatar='https://sambanova.ai/hubfs/logotype_sambanova_orange.png',
        ):
            formatted_ans = ans.replace('$', '\$')
            st.write(f'{formatted_ans}')


def setChatInputValue(chat_input_value: str) -> None:
    js = f"""
    <script>
        function insertText(dummy_var_to_force_repeat_execution) {{
            var chatInput = parent.document.querySelector('textarea[data-testid="stChatInputTextArea"]');
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype,
                "value"
            ).set;
            nativeInputValueSetter.call(chatInput, "{chat_input_value}");
            var event = new Event('input', {{ bubbles: true}});
            chatInput.dispatchEvent(event);
        }}
        insertText(3);
    </script>
    """
    st.components.v1.html(js)


def main() -> None:
    global output
    st.set_page_config(
        page_title='AI Starter Kit',
        page_icon='https://sambanova.ai/hubfs/logotype_sambanova_orange.png',
    )

    initialize_env_variables(prod_mode, additional_env_vars)

    st.title(':orange[SambaNova] Function Calling Assistant')

    if 'fc' not in st.session_state:
        st.session_state.fc = None

        # show overview message when function calling is not yet initialized
        with st.chat_message(
            'ai',
            avatar='https://sambanova.ai/hubfs/logotype_sambanova_orange.png',
        ):
            st.write(
                'This example application for function calling automates multi-step analysis by enabling language '
                'models to use information and operations from user-defined functions. While you can use any '
                'functions or database with the application, an example use case is implemented here: Uncovering '
                'trends in music sales using the provided sample database and tools. By leveraging natural language '
                'understanding, database interaction, code generation, and data visualization, the application '
                ' provides a real-world example of how models can use function calling to automate multi-step analysis '
                'tasks with accuracy.\n '
                'In addition to the sample DB of music sales, the application includes several tools that are '
                'available for the model to call as functions, some of the included tools are:\n'
                '- **query_db**: Allows users to interact with the sample music sales database via natural queries. '
                'You can ask questions about the data, such as "What are the top-selling albums of all time?" or "What '
                'is the total revenue from sales in a specific region?" The function will then retrieve the relevant '
                'data from the database and display the results.\n'
                '- **calculator**: Provides a simple calculator interface that allows the model to perform '
                'mathematical calculations using natural language inputs. The user can ask questions like "What is 10% '
                'of 100?" or "What is the sum of 2+2?" and the function will return the result.\n'
                '- **get_time**: Returns the current date and time for use in queries or calculations.'
            )

    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'tools' not in st.session_state:
        st.session_state.tools = [k for k, v in st_tools.items() if v['default'] and v['enabled']]
    if 'max_iterations' not in st.session_state:
        st.session_state.max_iterations = 5
    if 'input_disabled' not in st.session_state:
        st.session_state.input_disabled = True
    if 'session_temp_db' not in st.session_state:
        if prod_mode:
            st.session_state.session_temp_db = os.path.join(kit_dir, 'data', 'tmp_' + str(uuid.uuid4()), 'temp_db.db')
        else:
            st.session_state.session_temp_db = None

    with st.sidebar:
        st.title('Setup')

        # Callout to get SambaNova API Key
        st.markdown('Get your SambaNova API key [here](https://cloud.sambanova.ai/apis)')

        if not are_credentials_set(additional_env_vars):
            api_key, additional_vars = env_input_fields(additional_env_vars)
            if st.button('Save Credentials'):
                message = save_credentials(api_key, additional_vars, prod_mode)
                st.success(message)
                st.rerun()

        else:
            st.success('Credentials are set')
            if st.button('Clear Credentials'):
                save_credentials('', {var: '' for var in (additional_env_vars or [])}, prod_mode)
                st.rerun()

        if are_credentials_set(additional_env_vars):
            st.markdown('**1. Select the tools for function calling.**')
            st.session_state.tools = st.multiselect(
                'Available tools',
                [k for k, v in st_tools.items() if v['enabled']],
                [k for k, v in st_tools.items() if v['default'] and v['enabled']],
            )
            st.markdown('**2. Set the maximum number of iterations your want the model to run**')
            st.session_state.max_iterations = st.number_input('Max iterations', value=5, max_value=20)
            st.markdown('**Note:** The response cannot completed if the max number of iterations is too low')
            if st.button('Set'):
                with st.spinner('Processing'):
                    set_fc_llm(st.session_state.tools)
                    st.toast(f'Tool calling assistant set! Go ahead and ask some questions', icon='🎉')
                st.session_state.input_disabled = False

            st.markdown('**3. Ask the model**')

            with st.expander('**Execution scratchpad**', expanded=True):
                output = st.empty()  # type: ignore

            with st.expander('**Preset Example queries**', expanded=True):
                st.markdown('DB operations')
                for button_title, query in st_preset_queries.items():
                    if st.button(button_title):
                        setChatInputValue(query.strip())

            with st.expander('Additional settings', expanded=False):
                st.markdown('**Interaction options**')

                st.markdown('**Reset messages**')
                st.markdown('**Note:** Resetting the chat will clear all interactions history')
                if st.button('Reset messages history'):
                    st.session_state.chat_history = []
                    st.session_state.sources_history = []
                    st.toast('Interactions reset. The next response will clear the history on the screen')

    user_question = st.chat_input('Ask something', disabled=st.session_state.input_disabled, key='TheChatInput')
    handle_userinput(user_question)


if __name__ == '__main__':
    main()
