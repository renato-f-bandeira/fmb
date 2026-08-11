import geopandas as gpd
import pandas as pd
import numpy as np
import requests
import math
import time
import folium
from folium.features import GeoJsonTooltip
from datetime import datetime

print("1. Carregando as camadas do GeoPackage da Paraíba...")
gdf_poligonos = gpd.read_file('municipios_PB.gpkg', layer='lml_municipio_pb')
gdf_poligonos = gdf_poligonos.to_crs(epsg=4326)

gdf_pontos = gpd.read_file('municipios_PB.gpkg', layer='pontos_centroides_municipios')
gdf_pontos = gdf_pontos.to_crs(epsg=4326)

gdf_pontos['lat'] = gdf_pontos.geometry.y
gdf_pontos['lon'] = gdf_pontos.geometry.x

# Vamos imprimir os nomes das colunas para descobrirmos qual tem o nome da cidade!
print("COLUNAS DISPONÍVEIS NA CAMADA DE PONTOS:", gdf_pontos.columns.tolist())
print("COLUNAS DISPONÍVEIS NA CAMADA DE POLÍGONOS:", gdf_poligonos.columns.tolist())

print("2. Buscando dados climáticos na API com Sistema de Retentativas...")
gdf_pontos['Umidade_13h'] = 0.0
gdf_pontos['DSC'] = 0
gdf_pontos['Probabilidade_Fogo'] = 0.0
gdf_pontos['Classe_Risco'] = ''
gdf_pontos['Cor_Risco'] = ''

total_municipios = len(gdf_pontos)

for index, row in gdf_pontos.iterrows():
    lat = row['lat']
    lon = row['lon']
    
    # ATENÇÃO: Se descobrir o nome da coluna correto no log, troque 'NM_MUN' aqui embaixo
    nome_cidade = row.get('NM_MUN', f'Cidade_ID_{index}')
    
    print(f"Processando [{index + 1}/{total_municipios}]: {nome_cidade}...")
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=relative_humidity_2m&daily=precipitation_sum&past_days=90&forecast_days=1&timezone=America%2FSao_Paulo"
    
    sucesso = False
    for tentativa in range(3): # Tenta até 3 vezes antes de desistir
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status() 
            dados = response.json()
            sucesso = True
            break # Deu certo! Sai do loop de tentativas
        except Exception as e:
            print(f"  -> Falha na tentativa {tentativa + 1}. Aguardando 3s para tentar de novo...")
            time.sleep(3)
            
    if not sucesso:
        print(f"⚠️ Desistindo de {nome_cidade} após 3 tentativas.")
        gdf_pontos.at[index, 'Classe_Risco'] = 'Sem Dados'
        gdf_pontos.at[index, 'Cor_Risco'] = '#bdc3c7'
        continue # Pula para o próximo município

    # --- A. ENCONTRAR A UMIDADE ÀS 13H DE HOJE ---
    horas = dados['hourly']['time']
    umidades = dados['hourly']['relative_humidity_2m']
    hoje_str = datetime.now().strftime('%Y-%m-%d') + "T13:00"
    
    if hoje_str in horas:
        idx_13h = horas.index(hoje_str)
        umidade_hoje = umidades[idx_13h]
    else:
        umidade_hoje = np.nanmean(umidades[-12:-6])
        
    # --- B. CALCULAR DIAS SEM CHUVA (DSC) ---
    chuvas_diarias = dados['daily']['precipitation_sum']
    chuvas_passado = chuvas_diarias[:-1] 
    
    dsc = 0
    for chuva in reversed(chuvas_passado):
        if chuva is None or chuva <= 2.4:
            dsc += 1
        else:
            break
            
    # --- C. CALCULAR A EQUAÇÃO LOGÍSTICA (Índice Mata Branca) ---
    Z = 1.4285 - (0.1244 * umidade_hoje) + (0.0072 * dsc)
    probabilidade = (1 / (1 + math.exp(-Z))) * 100
    
    if probabilidade < 5.0:
        classe, cor = '1. Nulo', '#27ae60' 
    elif probabilidade < 10.0:
        classe, cor = '2. Baixo', '#f1c40f'
    elif probabilidade < 14.0:
        classe, cor = '3. Moderado', '#e67e22'
    elif probabilidade < 20.0:
        classe, cor = '4. Alto (Alerta)', '#e74c3c'
    elif probabilidade < 30.0:
        classe, cor = '5. Muito Alto', '#c0392b'
    else:
        classe, cor = '6. Crítico', '#8e44ad'

    gdf_pontos.at[index, 'Umidade_13h'] = umidade_hoje
    gdf_pontos.at[index, 'DSC'] = dsc
    gdf_pontos.at[index, 'Probabilidade_Fogo'] = round(probabilidade, 1)
    gdf_pontos.at[index, 'Classe_Risco'] = classe
    gdf_pontos.at[index, 'Cor_Risco'] = cor

    time.sleep(0.5) # Pausa padrão entre as cidades para respeitar a API

print("3. Unindo os resultados matemáticos aos polígonos do mapa...")
# ATENÇÃO: Troque 'NM_MUN' aqui se o nome da coluna no log for outro!
colunas_para_levar = ['NM_MUN', 'Umidade_13h', 'DSC', 'Probabilidade_Fogo', 'Classe_Risco', 'Cor_Risco']

# Para evitar o erro do "KeyError", vamos garantir que só filtramos se a coluna existir
colunas_existem = [col for col in colunas_para_levar if col in gdf_pontos.columns]
df_resultados_pontos = gdf_pontos[colunas_existem]

# ATENÇÃO: Troque 'NM_MUN' no parâmetro 'on' se necessário!
if 'NM_MUN' in df_resultados_pontos.columns and 'NM_MUN' in gdf_poligonos.columns:
    gdf_final = gdf_poligonos.merge(df_resultados_pontos, on='NM_MUN', how='left')
else:
    # Fallback caso NM_MUN não seja a coluna certa, junta pela ordem (index) temporariamente
    gdf_final = gdf_poligonos.copy()
    gdf_final = pd.concat([gdf_final, df_resultados_pontos.drop(columns=['geometry'], errors='ignore')], axis=1)

gdf_final['Cor_Risco'] = gdf_final.get('Cor_Risco', pd.Series(['#bdc3c7']*len(gdf_final))).fillna('#bdc3c7')
gdf_final['Classe_Risco'] = gdf_final.get('Classe_Risco', pd.Series(['Sem Dados']*len(gdf_final))).fillna('Sem Dados')

print("4. Preparando o Mapa Interativo...")
mapa_pb = folium.Map(location=[-7.115, -36.5], zoom_start=7, tiles='OpenStreetMap')

# Se NM_MUN não for a coluna correta, altere o 'fields' abaixo também
tooltip = GeoJsonTooltip(
    fields=['NM_MUN', 'Probabilidade_Fogo', 'Classe_Risco', 'DSC', 'Umidade_13h'] if 'NM_MUN' in gdf_final.columns else ['Probabilidade_Fogo', 'Classe_Risco', 'DSC', 'Umidade_13h'],
    aliases=['Município:', 'Risco de Fogo (%):', 'Classe:', 'Dias Sem Chuva:', 'Umidade às 13h (%):'] if 'NM_MUN' in gdf_final.columns else ['Risco de Fogo (%):', 'Classe:', 'Dias Sem Chuva:', 'Umidade às 13h (%):'],
    localize=True,
    sticky=False,
    labels=True,
    style="background-color: #F0EFEF; border: 2px solid black; border-radius: 3px; box-shadow: 3px;"
)

camada_municipios = folium.GeoJson(
    gdf_final,
    name='Municípios - Risco de Fogo',
    style_function=lambda feature: {
        'fillColor': feature['properties'].get('Cor_Risco', '#bdc3c7'),
        'color': 'black',
        'weight': 1,
        'fillOpacity': 0.7
    },
    tooltip=tooltip
).add_to(mapa_pb)

folium.LayerControl().add_to(mapa_pb)
mapa_html = mapa_pb._repr_html_()

print("5. Gerando Ranking do Top 10 e montando Dashboard HTML final...")
top_10 = gdf_final.sort_values(by='Probabilidade_Fogo', ascending=False).head(10)

coluna_nome = 'NM_MUN' if 'NM_MUN' in top_10.columns else top_10.columns[0]
tabela_html = top_10[[coluna_nome, 'Probabilidade_Fogo', 'Classe_Risco']].to_html(
    index=False, 
    classes='table table-striped table-hover table-sm',
    header=True
)

pagina_completa = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Painel de Risco - Índice Mata Branca</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body, html {{ height: 100%; margin: 0; padding: 0; }}
        .container-fluid {{ height: 100vh; }}
        .row {{ height: 100%; }}
        .sidebar {{ background-color: #f8f9fa; padding: 20px; overflow-y: auto; height: 100%; }}
        .map-container {{ padding: 0; height: 100%; }}
    </style>
</head>
<body>
    <div class="container-fluid">
        <div class="row">
            <div class="col-md-3 sidebar shadow">
                <h4 class="mb-4 text-danger fw-bold">🔥 Índice Mata Branca</h4>
                <p class="text-muted small">Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                <hr>
                <h6 class="fw-bold mb-3">⚠️ Top 10 Cidades em Risco</h6>
                {tabela_html}
                <hr>
                <p class="small text-muted mt-3">Metodologia: Equação de Regressão Logística baseada em Umidade Relativa e Dias Sem Chuva (DSC). <br><strong>Recomendado para uso operacional pela Defesa Civil da Paraíba.</strong></p>
            </div>
            <div class="col-md-9 map-container">
                {mapa_html}
            </div>
        </div>
    </div>
</body>
</html>
"""

with open('dashboard_operacional.html', 'w', encoding='utf-8') as f:
    f.write(pagina_completa)

print("✅ Dashboard gerado com sucesso: 'dashboard_operacional.html'")
