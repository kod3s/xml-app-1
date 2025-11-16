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
# INSERIR SCRIPT DO GOOGLE ADSENSE
# ==============================================
# Usando st.components.v1.html para incluir o script do Google AdSense
from streamlit.components.v1 import html

adsense_code = """
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-3204974273485445" 
        crossorigin="anonymous"></script>
<ins class="adsbygoogle"
     style="display:block"
     data-ad-client="ca-pub-3204974273485445"
     data-ad-slot="1234567890"
     data-ad-format="auto"></ins>
<script>
     (adsbygoogle = window.adsbygoogle || []).push({});
</script>
"""

# Inserindo o AdSense na página do Streamlit
html(adsense_code, height=250)

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
