import logging
import os
import shutil
import sys
import uuid
from typing import List, Optional

import streamlit as st
import yaml
from streamlit.runtime.uploaded_file_manager import UploadedFile

current_dir = os.path.dirname(os.path.abspath(__file__))
kit_dir = os.path.abspath(os.path.join(current_dir, '..'))
repo_dir = os.path.abspath(os.path.join(kit_dir, '..'))

sys.path.append(kit_dir)
sys.path.append(repo_dir)

from typing import Optional

from enterprise_knowledge_retriever.src.document_retrieval import DocumentRetrieval
from utils.events.mixpanel import MixpanelEvents
from utils.visual.env_utils import are_credentials_set, env_input_fields, initialize_env_variables, save_credentials

CONFIG_PATH = os.path.join(kit_dir, 'config.yaml')
PERSIST_DIRECTORY = os.path.join(kit_dir, f'data/my-vector-db')

logging.basicConfig(level=logging.INFO)
logging.info('URL: http://localhost:8501')


def save_files_user(docs: List[UploadedFile]) -> str:
    """
    Save all user uploaded files in Streamlit to the tmp dir with their file names

    Args:
        docs (List[UploadFile]): A list of uploaded files in Streamlit

    Returns:
        str: path where the files are saved.
    """

    # Create the data/tmp folder if it doesn't exist
    temp_folder = os.path.join(kit_dir, 'data/tmp')
    if not os.path.exists(temp_folder):
        os.makedirs(temp_folder)
    else:
        # If there are already files there, delete them
        for filename in os.listdir(temp_folder):
            file_path = os.path.join(temp_folder, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                print(f'Failed to delete {file_path}. Reason: {e}')

    # Save all selected files to the tmp dir with their file names
    for doc in docs:
        assert hasattr(doc, 'name'), 'doc has no attribute name.'
        assert callable(doc.getvalue), 'doc has no method getvalue.'
        temp_file = os.path.join(temp_folder, doc.name)
        with open(temp_file, 'wb') as f:
            f.write(doc.getvalue())

    return temp_folder


def handle_userinput(user_question: str) -> None:
    if user_question:
        try:
            with st.spinner('Processing...'):
                response = st.session_state.conversation.invoke({'question': user_question})
            st.session_state.chat_history.append(user_question)
            st.session_state.chat_history.append(response['answer'])

            sources = set([f'{sd.metadata["filename"]}' for sd in response['source_documents']])
            sources_text = ''
            for index, source in enumerate(sources, start=1):
                source_link = source
                sources_text += f'<font size="2" color="grey">{index}. {source_link}</font>  \n'
            st.session_state.sources_history.append(sources_text)
        except Exception as e:
            st.error(f'An error occurred while processing your question: {str(e)}')

    for ques, ans, source in zip(
        st.session_state.chat_history[::2],
        st.session_state.chat_history[1::2],
        st.session_state.sources_history,
    ):
        with st.chat_message('user'):
            st.write(f'{ques}')

        with st.chat_message(
            'ai',
            avatar='https://sambanova.ai/hubfs/logotype_sambanova_orange.png',
        ):
            st.write(f'{ans}')
            if st.session_state.show_sources:
                with st.expander('Sources'):
                    st.markdown(
                        f'<font size="2" color="grey">{source}</font>',
                        unsafe_allow_html=True,
                    )


def initialize_document_retrieval(prod_mode: bool) -> Optional[DocumentRetrieval]:
    if prod_mode:
        sambanova_api_key = st.session_state.SAMBANOVA_API_KEY
    else:
        if 'SAMBANOVA_API_KEY' in st.session_state:
            sambanova_api_key = os.environ.get('SAMBANOVA_API_KEY') or st.session_state.SAMBANOVA_API_KEY
        else:
            sambanova_api_key = os.environ.get('SAMBANOVA_API_KEY')
    if are_credentials_set():
        try:
            return DocumentRetrieval(sambanova_api_key=sambanova_api_key)
        except Exception as e:
            st.error(f'Failed to initialize DocumentRetrieval: {str(e)}')
            return None
    return None


def main() -> None:
    with open(CONFIG_PATH, 'r') as yaml_file:
        config = yaml.safe_load(yaml_file)

    prod_mode = config.get('prod_mode', False)
    llm_type = 'SambaStudio' if config['llm']['api'] == 'sambastudio' else 'SambaNova Cloud'
    conversational = config['retrieval'].get('conversational', False)
    default_collection = 'ekr_default_collection'

    initialize_env_variables(prod_mode)

    st.set_page_config(
        page_title='AI Starter Kit',
        page_icon='https://sambanova.ai/hubfs/logotype_sambanova_orange.png',
    )

    if 'conversation' not in st.session_state:
        st.session_state.conversation = None
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'show_sources' not in st.session_state:
        st.session_state.show_sources = True
    if 'sources_history' not in st.session_state:
        st.session_state.sources_history = []
    if 'vectorstore' not in st.session_state:
        st.session_state.vectorstore = None
    if 'input_disabled' not in st.session_state:
        st.session_state.input_disabled = True
    if 'document_retrieval' not in st.session_state:
        st.session_state.document_retrieval = None
    if 'st_session_id' not in st.session_state:
        st.session_state.st_session_id = str(uuid.uuid4())
    if 'mp_events' not in st.session_state:
        st.session_state.mp_events = MixpanelEvents(
            os.getenv('MIXPANEL_TOKEN'),
            st_session_id=st.session_state.st_session_id,
            kit_name='enterprise_knowledge_retriever',
            track=prod_mode,
        )
        st.session_state.mp_events.demo_launch()

    st.title(':orange[SambaNova] Analyst Assistant')

    with st.sidebar:
        st.title('Setup')

        # Callout to get SambaNova API Key
        st.markdown('Get your SambaNova API key [here](https://cloud.sambanova.ai/apis)')

        if not are_credentials_set():
            api_key, additional_vars = env_input_fields(mode=llm_type)
            if st.button('Save Credentials', key='save_credentials_sidebar'):
                message = save_credentials(api_key, additional_vars, prod_mode)
                st.session_state.mp_events.api_key_saved()
                st.success(message)
                st.rerun()
        else:
            st.success('Credentials are set')
            if st.button('Clear Credentials', key='clear_credentials'):
                save_credentials('', '', prod_mode)  # type: ignore
                st.rerun()

        if are_credentials_set():
            if st.session_state.document_retrieval is None:
                st.session_state.document_retrieval = initialize_document_retrieval(prod_mode)

        if st.session_state.document_retrieval is not None:
            st.markdown('**1. Pick a datasource**')

            # Conditionally set the options based on prod_mode
            datasource_options = ['Upload files (create new vector db)']
            if not prod_mode:
                datasource_options.append('Use existing vector db')

            datasource = st.selectbox('', datasource_options)

            if isinstance(datasource, str) and 'Upload' in datasource:
                if config.get('pdf_only_mode', False):
                    docs = st.file_uploader('Add PDF files', accept_multiple_files=True, type=['pdf'])
                else:
                    docs = st.file_uploader(
                        'Add files',
                        accept_multiple_files=True,
                        type=[
                            '.eml',
                            '.html',
                            '.json',
                            '.md',
                            '.msg',
                            '.rst',
                            '.rtf',
                            '.txt',
                            '.xml',
                            '.png',
                            '.jpg',
                            '.jpeg',
                            '.tiff',
                            '.bmp',
                            '.heic',
                            '.csv',
                            '.doc',
                            '.docx',
                            '.epub',
                            '.odt',
                            '.pdf',
                            '.ppt',
                            '.pptx',
                            '.tsv',
                            '.xlsx',
                        ],
                    )
                st.markdown('**2. Process your documents and create vector store**')
                st.markdown(
                    '**Note:** Depending on the size and number of your documents, this could take several minutes'
                )
                st.markdown('Create database')
                if st.button('Process'):
                    st.session_state.mp_events.input_submitted('document_ingest')
                    with st.spinner('Processing'):
                        try:
                            if docs is not None:
                                temp_folder = save_files_user(docs)
                            text_chunks = st.session_state.document_retrieval.parse_doc(temp_folder)
                            if len(text_chunks) == 0:
                                st.error(
                                    """No able to get text from the documents. check your docs or try setting
                                        pdf_only_mode to False"""
                                )
                            embeddings = st.session_state.document_retrieval.load_embedding_model()
                            collection_name = default_collection if not prod_mode else None
                            vectorstore = st.session_state.document_retrieval.create_vector_store(
                                text_chunks, embeddings, output_db=None, collection_name=collection_name
                            )
                            st.session_state.vectorstore = vectorstore
                            st.session_state.document_retrieval.init_retriever(vectorstore)
                            st.session_state.conversation = st.session_state.document_retrieval.get_qa_retrieval_chain(
                                conversational=conversational
                            )
                            st.toast(f'File uploaded! Go ahead and ask some questions', icon='🎉')
                            st.session_state.input_disabled = False
                        except Exception as e:
                            st.error(f'An error occurred while processing: {str(e)}')

                if not prod_mode:
                    st.markdown('[Optional] Save database for reuse')
                    save_location = st.text_input('Save location', './data/my-vector-db').strip()
                    if st.button('Process and Save database'):
                        with st.spinner('Processing'):
                            try:
                                if docs is not None:
                                    temp_folder = save_files_user(docs)
                                text_chunks = st.session_state.document_retrieval.parse_doc(temp_folder)
                                embeddings = st.session_state.document_retrieval.load_embedding_model()
                                vectorstore = st.session_state.document_retrieval.create_vector_store(
                                    text_chunks, embeddings, output_db=save_location, collection_name=default_collection
                                )
                                st.session_state.vectorstore = vectorstore
                                st.session_state.document_retrieval.init_retriever(vectorstore)
                                st.session_state.conversation = (
                                    st.session_state.document_retrieval.get_qa_retrieval_chain(
                                        conversational=conversational
                                    )
                                )
                                st.toast(
                                    f"""File uploaded and saved to {save_location} with collection
                                     '{default_collection}'! Go ahead and ask some questions""",
                                    icon='🎉',
                                )
                                st.session_state.input_disabled = False
                            except Exception as e:
                                st.error(f'An error occurred while processing and saving: {str(e)}')

            elif isinstance(datasource, str) and not prod_mode and 'Use existing' in datasource:
                db_path = st.text_input(
                    f'Absolute path to your DB folder',
                    placeholder='E.g., /Users/<username>/path/to/your/vectordb',
                ).strip()
                st.markdown('**2. Load your datasource and create vectorstore**')
                st.markdown('**Note:** Depending on the size of your vector database, this could take a few seconds')
                if st.button('Load'):
                    with st.spinner('Loading vector DB...'):
                        if db_path == '':
                            st.error('You must provide a path', icon='🚨')
                        else:
                            if os.path.exists(db_path):
                                try:
                                    embeddings = st.session_state.document_retrieval.load_embedding_model()
                                    collection_name = default_collection if not prod_mode else None
                                    vectorstore = st.session_state.document_retrieval.load_vdb(
                                        db_path, embeddings, collection_name=collection_name
                                    )
                                    st.toast(
                                        f"""Database loaded{'with collection '
                                         + default_collection if not prod_mode else ''}"""
                                    )
                                    st.session_state.vectorstore = vectorstore
                                    st.session_state.document_retrieval.init_retriever(vectorstore)
                                    st.session_state.conversation = (
                                        st.session_state.document_retrieval.get_qa_retrieval_chain(
                                            conversational=conversational
                                        )
                                    )
                                    st.session_state.input_disabled = False
                                except Exception as e:
                                    st.error(f'An error occurred while loading the database: {str(e)}')
                            else:
                                st.error('Database not present at ' + db_path, icon='🚨')
            st.markdown('**3. Ask questions about your data!**')

            with st.expander('Additional settings', expanded=True):
                st.markdown('**Interaction options**')
                st.markdown('**Note:** Toggle these at any time to change your interaction experience')
                show_sources = st.checkbox('Show sources', value=True, key='show_sources')

                st.markdown('**Reset chat**')
                st.markdown('**Note:** Resetting the chat will clear all conversation history')
                if st.button('Reset conversation'):
                    st.session_state.chat_history = []
                    st.session_state.sources_history = []
                    if not st.session_state.input_disabled:
                        st.session_state.conversation = st.session_state.document_retrieval.get_qa_retrieval_chain(
                            conversational=conversational
                        )
                    st.toast('Conversation reset. The next response will clear the history on the screen')
                    logging.info('Conversation reset')

    user_question = st.chat_input('Ask questions about your data', disabled=st.session_state.input_disabled)
    if user_question is not None:
        st.session_state.mp_events.input_submitted('chat_input')
        handle_userinput(user_question)


if __name__ == '__main__':
    main()
