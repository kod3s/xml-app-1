# streamlit_app.py
import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
import re
import tempfile
import io
import altair as alt
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------
# Config
# ---------------------------
st.set_page_config(page_title="CT-e Dashboard (Seguro)", layout="wide")

# Inicializa chaves de sessão
for key, default in {"logged_in": False, "user": None, "tabela": None, "is_admin": False}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------
# Conexão com BD (credenciais em st.secrets)
# ---------------------------
# Você deve adicionar em st.secrets (ou .streamlit/secrets.toml no ambiente local) a seção [database]
# com host, port, database, user, password. Nada no código.
db = st.secrets["database"]

engine = create_engine(
    f"postgresql+psycopg2://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['database']}"
)

# Cria tabelas de controle se não existirem:
with engine.begin() as conn:
    # tabela de usuários de aplicação (login)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS app_users (
            username VARCHAR(100) PRIMARY KEY,
            password_hash VARCHAR(300) NOT NULL,
            tabela VARCHAR(100) NOT NULL,
            is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """))

    # NOTA: as tabelas específicas por empresa (cte_*) serão criadas dinamicamente quando necessário.

# ---------------------------
# Funções de autenticação / administração
# ---------------------------
def get_user(username: str):
    with engine.connect() as conn:
        res = conn.execute(text("SELECT username, password_hash, tabela, is_admin FROM app_users WHERE username = :u"), {"u": username}).fetchone()
        return res

def create_user(username: str, password: str, tabela: str, is_admin: bool=False):
    pwd_hash = generate_password_hash(password)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app_users (username, password_hash, tabela, is_admin) VALUES (:u, :ph, :t, :adm)"),
            {"u": username, "ph": pwd_hash, "t": tabela, "adm": is_admin}
        )
        # cria a tabela da empresa se não existir
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {tabela} (
                id SERIAL PRIMARY KEY,
                data DATE,
                mes VARCHAR(20),
                numero_cte VARCHAR(40),
                transportador VARCHAR(200),
                placa VARCHAR(100),
                produto VARCHAR(200),
                cidade_origem VARCHAR(150),
                cidade_destino VARCHAR(150),
                quantidade_litros FLOAT,
                valor_frete DECIMAL(14,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

def delete_user(username: str):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_users WHERE username = :u"), {"u": username})

# Se não existem usuários no banco, criamos um admin inicial a partir das secrets:
# (Isso evita hardcoding; só ocorre se o banco estiver vazio)
with engine.connect() as conn:
    count = conn.execute(text("SELECT COUNT(*) FROM app_users")).scalar()
if count == 0:
    # espera-se que o deploy tenha em st.secrets a seção [admin] com 'username' e 'password'
    if "admin" in st.secrets and st.secrets["admin"].get("username") and st.secrets["admin"].get("password"):
        admin_user = st.secrets["admin"]["username"]
        admin_pass = st.secrets["admin"]["password"]
        # create initial admin with tabela padrão 'cte_admin'
        try:
            create_user(admin_user, admin_pass, tabela="cte_admin", is_admin=True)
            st.experimental_notice = f"Admin inicial criado: {admin_user}"
        except Exception as e:
            # se falhar, apenas ignore — pode haver concorrência de criação
            pass
    else:
        # sem admin nos secrets: o deployer precisará criar um usuário manualmente via DB
        pass

# ---------------------------
# Função para extrair dados do XML CT-e
# ---------------------------
def extrair_dados_cte(caminho_xml):
    try:
        tree = ET.parse(caminho_xml)
        root = tree.getroot()
        ns = {'cte': 'http://www.portalfiscal.inf.br/cte'}

        nCT = root.findtext(".//cte:ide/cte:nCT", namespaces=ns)
        data_emissao = root.findtext(".//cte:ide/cte:dhEmi", namespaces=ns)
        cidade_origem = root.findtext(".//cte:ide/cte:xMunIni", namespaces=ns)
        cidade_destino = root.findtext(".//cte:ide/cte:xMunFim", namespaces=ns)
        transportador = root.findtext(".//cte:emit/cte:xNome", namespaces=ns)
        produto = root.findtext(".//cte:infCarga/cte:proPred", namespaces=ns)
        unidade = root.findtext(".//cte:infCarga/cte:xOutCat", namespaces=ns)
        quantidade = root.findtext(".//cte:infCarga/cte:infQ/cte:qCarga", namespaces=ns)
        valor_frete = root.findtext(".//cte:vPrest/cte:vTPrest", namespaces=ns)
        inf_obs = root.findtext(".//cte:compl/cte:xObs", namespaces=ns)

        # Tratar data
        if data_emissao:
            data_emissao = datetime.strptime(data_emissao[:10], "%Y-%m-%d").date()
            mes = data_emissao.strftime("%B").capitalize()
        else:
            mes = ""
            data_emissao = None

        # Extrair placas do xObs
        placas = ""
        if inf_obs:
            padrao = re.compile(r"\b[A-Z]{3}\d{1,4}[A-Z0-9]{0,3}\b")
            matches = padrao.findall(inf_obs)
            if matches:
                placas = " ".join(matches)

        try:
            quantidade = float(quantidade) if quantidade else None
        except ValueError:
            quantidade = None
        try:
            valor_frete = float(valor_frete) if valor_frete else None
        except ValueError:
            valor_frete = None

        if unidade and unidade.strip().upper() in ["LITRO", "LT", "LTS"]:
            quantidade_litros = quantidade
        else:
            quantidade_litros = None

        return {
            "data": data_emissao,
            "mes": mes,
            "numero_cte": nCT,
            "transportador": transportador,
            "placa": placas,
            "produto": produto,
            "cidade_origem": cidade_origem,
            "cidade_destino": cidade_destino,
            "quantidade_litros": quantidade_litros,
            "valor_frete": valor_frete,
        }
    except Exception as e:
        st.warning(f"Erro ao processar {caminho_xml}: {e}")
        return None

# ---------------------------
# UI: Login
# ---------------------------
st.title("🚛 CT-e Dashboard (Seguro)")

if not st.session_state.logged_in:
    st.subheader("🔒 Faça login")
    username = st.text_input("Usuário")
    password = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        user_row = get_user(username)
        if user_row and check_password_hash(user_row["password_hash"], password):
            st.session_state.logged_in = True
            st.session_state.user = user_row["username"]
            st.session_state.tabela = user_row["tabela"]
            st.session_state.is_admin = user_row["is_admin"]
            st.success(f"Bem-vindo(a) {st.session_state.user}!")
            st.experimental_rerun()
        else:
            st.error("Usuário ou senha incorretos.")
    st.stop()

# ---------------------------
# UI: Painel Admin (apenas para is_admin)
# ---------------------------
if st.session_state.is_admin:
    st.sidebar.markdown("**Painel Admin**")
    if st.sidebar.checkbox("Abrir painel de administração"):
        st.subheader("Administração de Usuários / Empresas")

        # Form para criar novo usuário/empresa
        with st.form("form_create_user"):
            new_user = st.text_input("Novo usuário (login)")
            new_pass = st.text_input("Senha", type="password")
            new_table = st.text_input("Nome da tabela (ex: cte_empresa_x)")
            new_is_admin = st.checkbox("Usuário admin?", value=False)
            submit = st.form_submit_button("Criar usuário")
            if submit:
                if not (new_user and new_pass and new_table):
                    st.warning("Preencha usuário, senha e tabela.")
                else:
                    try:
                        create_user(new_user, new_pass, new_table, is_admin=new_is_admin)
                        st.success(f"Usuário criado: {new_user} → tabela {new_table}")
                    except Exception as e:
                        st.error(f"Erro ao criar usuário: {e}")

        st.markdown("---")
        # Listagem de usuários atuais
        with engine.connect() as conn:
            df_users = pd.read_sql("SELECT username, tabela, is_admin, created_at FROM app_users ORDER BY created_at DESC", conn)
        st.dataframe(df_users, use_container_width=True)

        # Remover usuário
        with st.form("form_delete_user"):
            del_user = st.text_input("Remover usuário (digite o login)")
            submit_del = st.form_submit_button("Remover")
            if submit_del:
                if del_user:
                    try:
                        delete_user(del_user)
                        st.success(f"Usuário {del_user} removido.")
                    except Exception as e:
                        st.error(f"Erro ao remover usuário: {e}")
                else:
                    st.warning("Digite um usuário para remover.")

# ---------------------------
# Interface principal do usuário logado
# ---------------------------
empresa = st.session_state.user
tabela = st.session_state.tabela

st.sidebar.success(f"Usuário: {empresa}")
st.sidebar.info(f"Tabela: {tabela}")

st.header(f"Dashboard CT-e — {empresa}")

# Upload de XMLs
arquivos = st.file_uploader("Selecione arquivos XML de CT-e", type=["xml"], accept_multiple_files=True)
if arquivos:
    dados = []
    for arquivo in arquivos:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xml") as tmp:
            tmp.write(arquivo.getbuffer())
            caminho_temp = tmp.name
        info = extrair_dados_cte(caminho_temp)
        if info:
            dados.append(info)

    if dados:
        df = pd.DataFrame(dados)
        # salvar no banco (tabela específica do usuário)
        try:
            df.to_sql(tabela, con=engine, if_exists="append", index=False)
            st.success("Dados inseridos com sucesso.")
        except Exception as e:
            st.error(f"Erro ao gravar no banco: {e}")

        st.dataframe(df, use_container_width=True)

        # exportar para excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="CTe")
        st.download_button(
            "📥 Exportar para Excel",
            data=buffer.getvalue(),
            file_name=f"{empresa}_cte_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# ---------------------------
# Dashboard: consultar dados já no banco
# ---------------------------
st.markdown("---")
st.subheader("Dados já salvos no banco")

with engine.connect() as conn:
    try:
        df_banco = pd.read_sql(f"SELECT * FROM {tabela} ORDER BY data DESC", conn)
    except Exception as e:
        df_banco = pd.DataFrame()
        st.error(f"Erro ao consultar dados: {e}")

if not df_banco.empty:
    st.metric("Total CT-e", len(df_banco))
    st.metric("Total litros", f"{df_banco['quantidade_litros'].sum(skipna=True):,.2f}")
    st.metric("Total frete (R$)", f"{df_banco['valor_frete'].sum(skipna=True):,.2f}")

    st.subheader("Litros por produto")
    grafico = (
        alt.Chart(df_banco)
        .mark_bar()
        .encode(x=alt.X("produto:N", sort='-y'), y=alt.Y("sum(quantidade_litros):Q"), tooltip=["produto", "sum(quantidade_litros)"])
        .properties(height=400)
    )
    st.altair_chart(grafico, use_container_width=True)

    st.subheader("Viagens por cidade de destino")
    viagens = df_banco.groupby("cidade_destino")["numero_cte"].count().reset_index(name="qtd")
    graf2 = (
        alt.Chart(viagens)
        .mark_bar()
        .encode(x=alt.X("cidade_destino:N", sort='-y'), y=alt.Y("qtd:Q"), tooltip=["cidade_destino", "qtd"])
        .properties(height=300)
    )
    st.altair_chart(graf2, use_container_width=True)
else:
    st.info("Nenhum dado encontrado para esta empresa. Faça upload de XMLs para começar.")
