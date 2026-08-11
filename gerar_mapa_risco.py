import geopandas as gpd
import pandas as pd
import numpy as np
import requests
import math
import folium
from folium.features import GeoJsonTooltip
from datetime import datetime

print("1. Carregando as camadas do GeoPackage da Paraíba...")
# Lendo a camada de POLÍGONOS (para desenhar o mapa)
gdf_poligonos = gpd.read_file('municipios_PB.gpkg', layer='lml_municipio_pb')
gdf_poligonos = gdf_poligonos.to_crs(epsg=4326)

# Lendo a camada de PONTOS (para buscar o clima na API)
gdf_pontos = gpd.read_file('municipios_PB.gpkg', layer='pontos_centroides_municipios')
gdf_pontos = gdf_pontos.to_crs(epsg=4326)

# Extraindo Lat/Lon diretamente da geometria da camada de pontos
gdf_pontos['lat'] = gdf_pontos.geometry.y
gdf_pontos['lon'] = gdf_pontos.geometry.x

print("2. Buscando dados climáticos na API Open-Meteo e calculando o Índice Mata Branca...")
# Criando colunas vazias na camada de pontos para guardar os resultados
gdf_pontos['Umidade_13h'] = 0.0
gdf_pontos['DSC'] = 0
gdf_pontos['Probabilidade_Fogo'] = 0.0
gdf_pontos['Classe_Risco'] = ''
gdf_pontos['Cor_Risco'] = ''

for index, row in gdf_pontos.iterrows():
    lat = row['lat']
    lon = row['lon']
    
    # URL da API Open-Meteo: Traz a chuva dos últimos 60 dias e a umidade de hoje
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=relative_humidity_2m&daily=precipitation_sum&past_days=60&forecast_days=1&timezone=America%2FSao_Paulo"
    
    try:
        response = requests.get(url)
        dados = response.json()
        
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
        
        # Determinar a Classe e a Cor
        if probabilidade < 5.0:
            classe, cor = '1. Nulo', '#27ae60' # Verde
        elif probabilidade < 10.0:
            classe, cor = '2. Baixo', '#f1c40f' # Amarelo
        elif probabilidade < 14.0:
            classe, cor = '3. Moderado', '#e67e22' # Laranja
        elif probabilidade < 20.0:
            classe, cor = '4. Alto (Alerta)', '#e74c3c' # Vermelho
        elif probabilidade < 30.0:
            classe, cor = '5. Muito Alto', '#c0392b' # Vinho
        else:
            classe, cor = '6. Crítico', '#8e44ad' # Roxo

        # Salva no DataFrame de PONTOS
        gdf_pontos.at[index, 'Umidade_13h'] = umidade_hoje
        gdf_pontos.at[index, 'DSC'] = dsc
        gdf_pontos.at[index, 'Probabilidade_Fogo'] = round(probabilidade, 1)
        gdf_pontos.at[index, 'Classe_Risco'] = classe
        gdf_pontos.at[index, 'Cor_Risco'] = cor

    except Exception as e:
        # Troque 'NM_MUN' abaixo se a sua coluna de nome tiver outro título
        print(f"Erro ao buscar dados para {row.get('NM_MUN', 'Município Desconhecido')}: {e}")

print("3. Unindo os resultados matemáticos aos polígonos do mapa...")
# Vamos cruzar (merge) os dados calculados nos pontos com a geometria dos polígonos
# IMPORTANTE: Confirme se a coluna que liga as duas tabelas se chama 'NM_MUN'
colunas_para_levar = ['NM_MUN', 'Umidade_13h', 'DSC', 'Probabilidade_Fogo', 'Classe_Risco', 'Cor_Risco']
df_resultados_pontos = gdf_pontos[colunas_interesse]

# O 'left merge' garante que o formato do mapa (polígonos) seja mantido intacto
gdf_final = gdf_poligonos.merge(df_resultados_pontos, on='NM_MUN', how='left')

# Preencher possíveis falhas caso algum município não cruze corretamente
gdf_final['Cor_Risco'] = gdf_final['Cor_Risco'].fillna('#bdc3c7') # Cinza
gdf_final['Classe_Risco'] = gdf_final['Classe_Risco'].fillna('Sem Dados')

print("4. Preparando o Mapa Interativo...")
mapa_pb = folium.Map(location=[-7.115, -36.5], zoom_start=7, tiles='OpenStreetMap')

tooltip = GeoJsonTooltip(
    fields=['NM_MUN', 'Probabilidade_Fogo', 'Classe_Risco', 'DSC', 'Umidade_13h'],
    aliases=['Município:', 'Risco de Fogo (%):', 'Classe:', 'Dias Sem Chuva:', 'Umidade às 13h (%):'],
    localize=True,
    sticky=False,
    labels=True,
    style="""
        background-color: #F0EFEF;
        border: 2px solid black;
        border-radius: 3px;
        box-shadow: 3px;
    """
)

camada_municipios = folium.GeoJson(
    gdf_final,
    name='Municípios - Risco de Fogo',
    style_function=lambda feature: {
        'fillColor': feature['properties']['Cor_Risco'],
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

tabela_html = top_10[['NM_MUN', 'Probabilidade_Fogo', 'Classe_Risco']].to_html(
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
                <p class="small text-muted mt-3">Metodologia: Equação de Regressão Logística baseada em Umidade Relativa e Dias Sem Chuva (DSC). <br><strong>Recomendado para uso operacional pela Defesa Civil.</strong></p>
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
