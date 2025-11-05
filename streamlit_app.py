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

# ==============================================
# CONFIGURAÇÃO INICIAL
# ==============================================
st.set_page_config(page_title="CT-e Dashboard (Seguro)", layout="wide")

# Inicializa variáveis de sessão
for key, default in {"logged_in": False, "user": None, "tabela": None, "is_admin": False}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ==============================================
# CONEXÃO (credenciais EM st.secrets — NÃO colocar no código)
# ==============================================
# Exige que você defina st.secrets["database"] com host, port, database, user, password
try:
    db = st.secrets["database"]
except Exception as e:
    st.error("🔒 Erro: secrets não configurados. Configure [database] em st.secrets (Streamlit Cloud) ou .streamlit/secrets.toml localmente.")
    st.stop()

engine = create_engine(
    f"postgresql+psycopg2://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['database']}",
    pool_pre_ping=True
)

# Teste rápido de conexão (mostra no sidebar)
try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    st.sidebar.success("✅ Conexão com banco OK")
except Exception as e:
    st.sidebar.error(f"❌ Falha na conexão com o banco: {e}")
    st.stop()

# ==============================================
# HELPERS: validação de nome de tabela e criação
# ==============================================
def is_valid_table_name(name: str) -> bool:
    # aceita apenas letras, números e underscore; evita SQL injection via nomes de tabela
    return bool(re.match(r"^[a-zA-Z0-9_]+$", name))

def ensure_table_exists(tabela: str):
    if not is_valid_table_name(tabela):
        raise ValueError("Nome de tabela inválido. Use apenas letras, números e underscore (_).")
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

# ==============================================
# Cria tabela de controle de usuários (app_users)
# ==============================================
with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS app_users (
            username VARCHAR(100) PRIMARY KEY,
            password_hash VARCHAR(300) NOT NULL,
            tabela VARCHAR(100) NOT NULL,
            is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """))

# ==============================================
# FUNÇÕES: get/create/delete users
# ==============================================
def get_user(username: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT username, password_hash, tabela, is_admin FROM app_users WHERE username = :u"),
            {"u": username}
        ).mappings().fetchone()
        return row  # None ou RowMapping

def create_user(username: str, password: str, tabela: str, is_admin: bool=False):
    if not username or not tabela:
        raise ValueError("Usuário e tabela são obrigatórios.")
    if not is_valid_table_name(tabela):
        raise ValueError("Nome de tabela inválido. Use apenas letras, números e underscore (_).")
    pwd_hash = generate_password_hash(password)
    with engine.begin() as conn:
        # evita duplicidade por username
        conn.execute(
            text("""
                INSERT INTO app_users (username, password_hash, tabela, is_admin)
                VALUES (:u, :ph, :t, :adm)
                ON CONFLICT (username) DO NOTHING
            """),
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
    if not username:
        raise ValueError("Usuário inválido.")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_users WHERE username = :u"), {"u": username})

# ==============================================
# Cria admin inicial caso tabela app_users esteja vazia
# admin credenciais devem vir de st.secrets["admin"]
# ==============================================
with engine.connect() as conn:
    count = conn.execute(text("SELECT COUNT(*) FROM app_users")).scalar()

if count == 0:
    if "admin" in st.secrets and st.secrets["admin"].get("username") and st.secrets["admin"].get("password"):
        try:
            create_user(
                st.secrets["admin"]["username"],
                st.secrets["admin"]["password"],
                tabela="cte_admin",
                is_admin=True
            )
            st.sidebar.info("Admin inicial criado (a partir de Secrets).")
        except Exception:
            # se houver concorrência de criação, ignore silenciosamente
            pass
    else:
        st.sidebar.warning("Nenhum admin definido em Secrets; crie um usuário admin manualmente no DB.")

# ==============================================
# FUNÇÃO: extrair dados do XML CT-e
# ==============================================
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
        except Exception:
            quantidade = None
        try:
            valor_frete = float(valor_frete) if valor_frete else None
        except Exception:
            valor_frete = None

        quantidade_litros = quantidade if unidade and unidade.strip().upper() in ["LITRO", "LT", "LTS"] else None

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

# ==============================================
# UI: Login
# ==============================================
st.title("🚛 CT-e Dashboard (Seguro)")

if not st.session_state.logged_in:
    st.subheader("🔒 Faça login")
    username = st.text_input("Usuário")
    password = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        user_row = get_user(username)
        if user_row is None:
            st.error("Usuário não encontrado.")
        else:
            try:
                if check_password_hash(user_row["password_hash"], password):
                    st.session_state.logged_in = True
                    st.session_state.user = user_row["username"]
                    st.session_state.tabela = user_row["tabela"]
                    st.session_state.is_admin = user_row["is_admin"]
                    st.success(f"Bem-vindo(a) {st.session_state.user}!")
                    try:
                        st.rerun()
                    except AttributeError:
                        st.experimental_rerun()
                else:
                    st.error("Senha incorreta.")
            except Exception as e:
                st.error(f"Erro ao verificar senha: {e}")
    st.stop()

# ==============================================
# Painel Admin
# ==============================================
if st.session_state.is_admin:
    st.sidebar.markdown("**⚙️ Painel Admin**")
    if st.sidebar.checkbox("Gerenciar usuários"):
        st.subheader("Administração de Usuários / Empresas")

        with st.form("form_create_user"):
            new_user = st.text_input("Novo usuário (login)")
            new_pass = st.text_input("Senha", type="password")
            new_table = st.text_input("Tabela (ex: cte_empresa_x)")
            new_is_admin = st.checkbox("Usuário admin?", value=False)
            submit = st.form_submit_button("Criar usuário")
            if submit:
                if not (new_user and new_pass and new_table):
                    st.warning("Preencha usuário, senha e tabela.")
                else:
                    try:
                        create_user(new_user, new_pass, new_table, is_admin=new_is_admin)
                        st.success(f"Usuário criado: {new_user} → {new_table}")
                    except Exception as e:
                        st.error(f"Erro ao criar usuário: {e}")

        st.markdown("---")
        with engine.connect() as conn:
            df_users = pd.read_sql("SELECT username, tabela, is_admin, created_at FROM app_users ORDER BY created_at DESC", conn)
        st.dataframe(df_users, use_container_width=True)

        st.markdown("---")
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

# ==============================================
# Interface principal do usuário autenticado
# ==============================================
empresa = st.session_state.user
tabela = st.session_state.tabela

st.sidebar.success(f"Usuário: {empresa}")
st.sidebar.info(f"Tabela: {tabela}")

st.header(f"📦 Leitor e Dashboard de CT-e — {empresa}")

# Upload de XMLs
arquivos = st.file_uploader("Selecione arquivos XML de CT-e", type=["xml"], accept_multiple_files=True)

if arquivos:
    dados_extraidos = []
    for arquivo in arquivos:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xml") as tmp:
            tmp.write(arquivo.getbuffer())
            caminho_temp = tmp.name
        info = extrair_dados_cte(caminho_temp)
        if info:
            dados_extraidos.append(info)

    if dados_extraidos:
        df = pd.DataFrame(dados_extraidos)
        st.subheader("📋 Dados Extraídos (pré-visualização)")
        st.dataframe(df, use_container_width=True)

        # garante que tabela existe antes de inserir
        try:
            ensure_table_exists(tabela)
        except Exception as e:
            st.error(f"Nome de tabela inválido ou erro ao criar tabela: {e}")
            st.stop()

        # inserir dados
        try:
            df.to_sql(tabela, con=engine, if_exists="append", index=False, method="multi")
            st.success(f"✅ {len(df)} registros inseridos com sucesso na tabela **{tabela}**!")
        except Exception as e:
            st.error(f"❌ Erro ao gravar no banco: {e}")

        # exportar para excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="CTe")
        st.download_button(
            label="📥 Exportar para Excel",
            data=buffer.getvalue(),
            file_name=f"{empresa}_cte_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# ==============================================
# Dashboard: ler dados da tabela e gerar gráficos
# ==============================================
st.markdown("---")
st.subheader("📈 Dashboard de Transporte")

with engine.connect() as conn:
    try:
        df_banco = pd.read_sql(f"SELECT * FROM {tabela} ORDER BY data DESC", conn)
        st.write(f"🔎 {len(df_banco)} registros encontrados na tabela `{tabela}`")
    except Exception as e:
        st.error(f"Erro ao consultar dados: {e}")
        df_banco = pd.DataFrame()

if not df_banco.empty:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("🧾 Total de CT-es", len(df_banco))
    with col2:
        st.metric("⛽ Total (L)", f"{df_banco['quantidade_litros'].sum(skipna=True):,.2f}")
    with col3:
        st.metric("💰 Valor Total Frete", f"R$ {df_banco['valor_frete'].sum(skipna=True):,.2f}")

    st.markdown("### 📦 Litros por Produto")
    grafico_litros = (
        alt.Chart(df_banco)
        .mark_bar(color="#4C9AFF")
        .encode(
            x=alt.X("produto:N", sort='-y'),
            y=alt.Y("sum(quantidade_litros):Q", title="Total de Litros"),
            tooltip=["produto", "sum(quantidade_litros)"]
        )
    )
    st.altair_chart(grafico_litros, use_container_width=True)

    st.markdown("### 🏙️ Viagens por Cidade de Destino")
    viagens_cidade = df_banco.groupby("cidade_destino")["numero_cte"].count().reset_index(name="QTD")
    grafico_cidade = (
        alt.Chart(viagens_cidade)
        .mark_bar(color="#FFA500")
        .encode(
            x=alt.X("cidade_destino:N", sort='-y'),
            y=alt.Y("QTD:Q"),
            tooltip=["cidade_destino", "QTD"]
        )
    )
    st.altair_chart(grafico_cidade, use_container_width=True)
else:
    st.info("📭 Nenhum dado salvo ainda. Faça upload de XMLs para ver o dashboard.")
