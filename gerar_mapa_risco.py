import geopandas as gpd
import pandas as pd
import numpy as np
import requests
import math
import folium
from folium.features import GeoJsonTooltip
from datetime import datetime

print("1. Carregando o mapa dos municípios da Paraíba...")
# Substitua pelo caminho do seu arquivo GeoPackage ou Shapefile
gdf = gpd.read_file('municipios_pb.gpkg') 

# Converte para projeção global (Lat/Lon) caso não esteja, essencial para o Folium
gdf = gdf.to_crs(epsg=4326)

# Calcula o centroide (ponto central) de cada município automaticamente
gdf['centroide'] = gdf.geometry.centroid
gdf['lat'] = gdf['centroide'].y
gdf['lon'] = gdf['centroide'].x

print("2. Buscando dados climáticos na API Open-Meteo e calculando o Índice Mata Branca...")
# Vamos criar colunas vazias para guardar os resultados
gdf['Umidade_13h'] = 0.0
gdf['DSC'] = 0
gdf['Probabilidade_Fogo'] = 0.0
gdf['Classe_Risco'] = ''
gdf['Cor_Risco'] = ''

for index, row in gdf.iterrows():
    lat = row['lat']
    lon = row['lon']
    
    # URL da API Open-Meteo: Traz a chuva dos últimos 60 dias e a umidade de hoje
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=relative_humidity_2m&daily=precipitation_sum&past_days=60&forecast_days=1&timezone=America%2FSao_Paulo"
    
    try:
        response = requests.get(url)
        dados = response.json()
        
        # --- A. ENCONTRAR A UMIDADE ÀS 13H DE HOJE ---
        # Pega a lista de horas e umidades
        horas = dados['hourly']['time']
        umidades = dados['hourly']['relative_humidity_2m']
        
        # Filtra a string para achar o dia de hoje às 13:00
        hoje_str = datetime.now().strftime('%Y-%m-%d') + "T13:00"
        
        if hoje_str in horas:
            idx_13h = horas.index(hoje_str)
            umidade_hoje = umidades[idx_13h]
        else:
            # Fallback de segurança: pega a média das umidades da tarde
            umidade_hoje = np.nanmean(umidades[-12:-6])
            
        # --- B. CALCULAR DIAS SEM CHUVA (DSC) ---
        chuvas_diarias = dados['daily']['precipitation_sum']
        # Removemos o dia de hoje da contagem de chuva para calcular o passado recente
        chuvas_passado = chuvas_diarias[:-1] 
        
        dsc = 0
        # Conta de trás para frente (de ontem para os dias anteriores)
        for chuva in reversed(chuvas_passado):
            if chuva is None or chuva <= 2.4: # Limiar clássico de abatimento de chuva
                dsc += 1
            else:
                break
                
        # --- C. CALCULAR A SUA EQUAÇÃO LOGÍSTICA ---
        # Equação Operacional do Índice Mata Branca
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

        # Salva no DataFrame
        gdf.at[index, 'Umidade_13h'] = umidade_hoje
        gdf.at[index, 'DSC'] = dsc
        gdf.at[index, 'Probabilidade_Fogo'] = round(probabilidade, 1)
        gdf.at[index, 'Classe_Risco'] = classe
        gdf.at[index, 'Cor_Risco'] = cor

    except Exception as e:
        print(f"Erro ao buscar dados para {row['NM_MUN']}: {e}")

print("3. Preparando o Mapa Interativo...")
# Centraliza o mapa na Paraíba
mapa_pb = folium.Map(location=[-7.115, -36.5], zoom_start=7, tiles='OpenStreetMap')

# Cria um layer de polígonos com as cores do risco e Tooltip (interatividade)
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

# Adiciona os polígonos ao mapa
camada_municipios = folium.GeoJson(
    gdf,
    name='Municípios - Risco de Fogo',
    style_function=lambda feature: {
        'fillColor': feature['properties']['Cor_Risco'],
        'color': 'black', # Cor da borda
        'weight': 1,
        'fillOpacity': 0.7
    },
    tooltip=tooltip
).add_to(mapa_pb)

# Adiciona o controle para ligar e desligar camadas (Botão no canto superior direito)
folium.LayerControl().add_to(mapa_pb)

# Salva o mapa Folium base como string HTML
mapa_html = mapa_pb._repr_html_()

print("4. Gerando Ranking do Top 10 e montando Dashboard HTML final...")
# Pega os 10 municípios com maior probabilidade de fogo
top_10 = gdf.sort_values(by='Probabilidade_Fogo', ascending=False).head(10)

# Monta o HTML da tabela Top 10
tabela_html = top_10[['NM_MUN', 'Probabilidade_Fogo', 'Classe_Risco']].to_html(
    index=False, 
    classes='table table-striped table-hover table-sm',
    header=True
)

# Template HTML completo dividindo a tela em duas partes (Sidebar e Mapa)
pagina_completa = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Painel de Risco - Índice Mata Branca</title>
    <!-- Adicionando Bootstrap para o layout ficar bonito automaticamente -->
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
            <!-- Barra Lateral com a Tabela -->
            <div class="col-md-3 sidebar shadow">
                <h4 class="mb-4 text-danger fw-bold">🔥 Índice Mata Branca</h4>
                <p class="text-muted small">Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                <hr>
                <h6 class="fw-bold mb-3">⚠️ Top 10 Cidades em Risco</h6>
                {tabela_html}
                <hr>
                <p class="small text-muted mt-3">Metodologia: Equação de Regressão Logística baseada em Umidade Relativa e Dias Sem Chuva (DSC). <br><strong>Recomendado para uso operacional pela Defesa Civil.</strong></p>
            </div>
            
            <!-- Área do Mapa Interativo -->
            <div class="col-md-9 map-container">
                {mapa_html}
            </div>
        </div>
    </div>
</body>
</html>
"""

# Salva o arquivo final
with open('dashboard_operacional.html', 'w', encoding='utf-8') as f:
    f.write(pagina_completa)

print("✅ Dashboard gerado com sucesso: 'dashboard_operacional.html'")