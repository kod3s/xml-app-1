import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
import tempfile
import io
import re

# ==============================================
# CONFIGURAÇÃO INICIAL
# ==============================================
st.set_page_config(page_title="CT-e to Excel", layout="wide")

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
# IMPORTAÇÃO E EXIBIÇÃO DE XMLs
# ==============================================
st.title("📦 Importar CT-e para Excel")

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
        # Exibindo os dados extraídos
        df = pd.DataFrame(registros)
        st.dataframe(df)

        # Gerar Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="CTe")
        
        # Exibir botão para download do Excel
        st.download_button("📥 Exportar para Excel", buffer.getvalue(), file_name="cte_exportado.xlsx")

else:
    st.info("Nenhum arquivo XML foi carregado. Selecione um arquivo para continuar.")
