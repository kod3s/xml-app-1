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

# ==============================================
# CONFIGURAÇÃO INICIAL
# ==============================================
st.set_page_config(page_title="CT-e Dashboard (Seguro)", layout="wide")

# Inicializa variáveis de sessão
for key, default in {"logged_in": False, "user": None, "tabela": None, "is_admin": False}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ==============================================
# CONEXÃO COM BANCO (credenciais via st.secrets)
# ==============================================
try:
    db = st.secrets["database"]
except Exception:
    st.error("🔒 Configure as credenciais [database] em st.secrets (Streamlit Cloud ou local).")
    st.stop()

engine = create_engine(
    f"postgresql+psycopg2://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['database']}",
    pool_pre_ping=True
)

# ==============================================
# VERIFICA CONEXÃO
# ==============================================
try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    st.sidebar.success("✅ Conectado ao banco de dados")
except Exception as e:
    st.sidebar.error(f"❌ Erro de conexão: {e}")
    st.stop()

# ==============================================
# CRIA TABELAS NECESSÁRIAS
# ==============================================
with engine.begin() as conn:
    # tabela principal de usuários
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS app_users (
            username VARCHAR(100) PRIMARY KEY,
            password_hash VARCHAR(300) NOT NULL,
            tabela VARCHAR(100) NOT NULL,
            is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """))
    # tabela geral de registros (consolida tudo)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cte_todos (
            id SERIAL PRIMARY KEY,
            empresa VARCHAR(100),
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

# ==============================================
# FUNÇÕES AUXILIARES
# ==============================================
def is_valid_table_name(name: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_]+$", name))

def ensure_table_exists(tabela: str):
    if not is_valid_table_name(tabela):
        raise ValueError("Nome de tabela inválido.")
    with engine.begin() as conn:
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

def get_user(username: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT username, password_hash, tabela, is_admin FROM app_users WHERE username = :u"),
            {"u": username}
        ).mappings().fetchone()

def create_user(username: str, password: str, tabela: str, is_admin: bool = False):
    pwd_hash = generate_password_hash(password)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO app_users (username, password_hash, tabela, is_admin)
                VALUES (:u, :ph, :t, :adm)
                ON CONFLICT (username) DO NOTHING
            """),
            {"u": username, "ph": pwd_hash, "t": tabela, "adm": is_admin}
        )
    ensure_table_exists(tabela)

def delete_user(username: str):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_users WHERE username = :u"), {"u": username})

# cria admin inicial, se necessário
with engine.connect() as conn:
    count = conn.execute(text("SELECT COUNT(*) FROM app_users")).scalar()
if count == 0 and "admin" in st.secrets:
    adm = st.secrets["admin"]
    if adm.get("username") and adm.get("password"):
        create_user(adm["username"], adm["password"], tabela="cte_admin", is_admin=True)
        st.sidebar.info("👑 Admin inicial criado.")

# ==============================================
# FUNÇÃO: extrair dados do XML
# ==============================================
def extrair_dados_cte(caminho_xml):
    try:
        ns = {'cte': 'http://www.portalfiscal.inf.br/cte'}
        tree = ET.parse(caminho_xml)
        root = tree.getroot()

        data_emissao = root.findtext(".//cte:ide/cte:dhEmi", namespaces=ns)
        data_emissao = datetime.strptime(data_emissao[:10], "%Y-%m-%d").date() if data_emissao else None
        mes = data_emissao.strftime("%B").capitalize() if data_emissao else ""

        return {
            "data": data_emissao,
            "mes": mes,
            "numero_cte": root.findtext(".//cte:ide/cte:nCT", namespaces=ns),
            "transportador": root.findtext(".//cte:emit/cte:xNome", namespaces=ns),
            "placa": " ".join(re.findall(r"[A-Z]{3}\d{1,4}[A-Z0-9]{0,3}", root.findtext(".//cte:compl/cte:xObs", namespaces=ns) or "")),
            "produto": root.findtext(".//cte:infCarga/cte:proPred", namespaces=ns),
            "cidade_origem": root.findtext(".//cte:ide/cte:xMunIni", namespaces=ns),
            "cidade_destino": root.findtext(".//cte:ide/cte:xMunFim", namespaces=ns),
            "quantidade_litros": float(root.findtext(".//cte:infCarga/cte:infQ/cte:qCarga", namespaces=ns) or 0),
            "valor_frete": float(root.findtext(".//cte:vPrest/cte:vTPrest", namespaces=ns) or 0)
        }
    except Exception as e:
        st.warning(f"Erro ao processar XML: {e}")
        return None

# ==============================================
# LOGIN
# ==============================================
st.title("🚛 CT-e Dashboard (Seguro)")

if not st.session_state.logged_in:
    st.subheader("🔒 Login de Acesso")
    username = st.text_input("Usuário")
    password = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        user = get_user(username)
        if user and check_password_hash(user["password_hash"], password):
            st.session_state.logged_in = True
            st.session_state.user = user["username"]
            st.session_state.tabela = user["tabela"]
            st.session_state.is_admin = user["is_admin"]
            st.success(f"Bem-vindo(a), {username}!")
            st.rerun()
        else:
            st.error("Usuário ou senha incorretos.")
    st.stop()

# ==============================================
# PAINEL ADMIN
# ==============================================
if st.session_state.is_admin:
    st.sidebar.markdown("### ⚙️ Painel Admin")
    if st.sidebar.checkbox("Gerenciar usuários"):
        st.subheader("Administração de Usuários")
        new_user = st.text_input("Novo usuário")
        new_pass = st.text_input("Senha", type="password")
        new_tab = st.text_input("Tabela (ex: cte_empresa)")
        isadm = st.checkbox("Administrador?")
        if st.button("Criar usuário"):
            try:
                create_user(new_user, new_pass, new_tab, is_admin=isadm)
                st.success(f"Usuário criado: {new_user}")
            except Exception as e:
                st.error(e)
        st.markdown("---")
        with engine.connect() as conn:
            dfu = pd.read_sql("SELECT username, tabela, is_admin, created_at FROM app_users", conn)
        st.dataframe(dfu)
        del_user = st.text_input("Usuário para remover")
        if st.button("Remover usuário"):
            delete_user(del_user)
            st.success("Usuário removido.")

# ==============================================
# ÁREA DO USUÁRIO LOGADO
# ==============================================
empresa = st.session_state.user
tabela = st.session_state.tabela

st.sidebar.success(f"Usuário: {empresa}")
st.sidebar.info(f"Tabela: {tabela}")

st.header(f"📦 Importar CT-e — {empresa}")

arquivos = st.file_uploader("Selecione XMLs", type=["xml"], accept_multiple_files=True)
if arquivos:
    registros = []
    for arq in arquivos:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xml") as tmp:
            tmp.write(arq.getbuffer())
            tmp.flush()
            info = extrair_dados_cte(tmp.name)
            if info:
                registros.append(info)

    if registros:
        df = pd.DataFrame(registros)
        st.dataframe(df)
        ensure_table_exists(tabela)
        try:
            df.to_sql(tabela, con=engine, if_exists="append", index=False)
            df["empresa"] = empresa
            df.to_sql("cte_todos", con=engine, if_exists="append", index=False)
            st.success(f"{len(df)} registros gravados com sucesso!")
        except Exception as e:
            st.error(f"Erro ao salvar: {e}")

# ==============================================
# DASHBOARD E FILTROS
# ==============================================
st.markdown("---")
st.subheader("📊 Dashboard de Transportes")

query = f"SELECT * FROM {tabela}"
with engine.connect() as conn:
    try:
        df_banco = pd.read_sql(query, conn)
    except Exception as e:
        st.error(f"Erro ao consultar: {e}")
        df_banco = pd.DataFrame()

if not df_banco.empty:
    # FILTROS
    colf1, colf2, colf3, colf4, colf5 = st.columns(5)
    with colf1:
        data_min, data_max = df_banco["data"].min(), df_banco["data"].max()
        periodo = st.date_input("Período", (data_min, data_max))
    with colf2:
        origem = st.selectbox("Cidade Origem", ["Todas"] + sorted(df_banco["cidade_origem"].dropna().unique().tolist()))
    with colf3:
        destino = st.selectbox("Cidade Destino", ["Todas"] + sorted(df_banco["cidade_destino"].dropna().unique().tolist()))
    with colf4:
        produto = st.selectbox("Produto", ["Todos"] + sorted(df_banco["produto"].dropna().unique().tolist()))
    with colf5:
        fornecedor = st.selectbox("Transportador", ["Todos"] + sorted(df_banco["transportador"].dropna().unique().tolist()))

    filtro = (df_banco["data"].between(periodo[0], periodo[1]))
    if origem != "Todas":
        filtro &= (df_banco["cidade_origem"] == origem)
    if destino != "Todas":
        filtro &= (df_banco["cidade_destino"] == destino)
    if produto != "Todos":
        filtro &= (df_banco["produto"] == produto)
    if fornecedor != "Todos":
        filtro &= (df_banco["transportador"] == fornecedor)

    df_filtrado = df_banco[filtro]

    # KPIs
    col1, col2, col3 = st.columns(3)
    col1.metric("CT-es", len(df_filtrado))
    col2.metric("Total Litros", f"{df_filtrado['quantidade_litros'].sum():,.2f}")
    col3.metric("Frete Total", f"R$ {df_filtrado['valor_frete'].sum():,.2f}")

    # GRÁFICOS
    st.markdown("### 📦 Litros por Produto")
    chart1 = alt.Chart(df_filtrado).mark_bar(color="#4C9AFF").encode(
        x=alt.X("produto:N", sort='-y'),
        y="sum(quantidade_litros):Q",
        tooltip=["produto", "sum(quantidade_litros)"]
    )
    st.altair_chart(chart1, use_container_width=True)

    st.markdown("### 🏙️ Viagens por Cidade de Destino")
    viagens = df_filtrado.groupby("cidade_destino")["numero_cte"].count().reset_index(name="QTD")
    chart2 = alt.Chart(viagens).mark_bar(color="#FFA500").encode(
        x=alt.X("cidade_destino:N", sort='-y'),
        y="QTD:Q",
        tooltip=["cidade_destino", "QTD"]
    )
    st.altair_chart(chart2, use_container_width=True)

    # EXPORTAÇÃO
    st.markdown("---")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_filtrado.to_excel(writer, index=False, sheet_name="CTe")
    st.download_button("📥 Exportar registros filtrados", buffer.getvalue(),
                       file_name=f"{empresa}_cte_filtrado.xlsx")

    if st.session_state.is_admin:
        st.info("Admin: exportar todos os registros consolidados (cte_todos).")
        with engine.connect() as conn:
            df_all = pd.read_sql("SELECT * FROM cte_todos ORDER BY created_at DESC", conn)
        buf2 = io.BytesIO()
        with pd.ExcelWriter(buf2, engine="openpyxl") as writer:
            df_all.to_excel(writer, index=False, sheet_name="CTe_Todos")
        st.download_button("📦 Exportar todos os registros", buf2.getvalue(),
                           file_name="cte_todos.xlsx")
else:
    st.info("Nenhum dado disponível. Faça upload de XMLs para iniciar.")
