import os
import geopandas as gpd
import pandas as pd
import numpy as np
import requests
import math
import time
import folium
import pytz
import pyogrio
from folium.features import GeoJsonTooltip
from datetime import datetime

# --- CONFIGURAÇÃO DE TEMPO (FUSO DE BRASÍLIA) ---
fuso_br = pytz.timezone('America/Sao_Paulo')
agora_br = datetime.now(fuso_br)
data_hoje_str = agora_br.strftime('%d/%m/%Y')
hora_exibicao = agora_br.strftime('%d/%m/%Y %H:%M')

print("1. Carregando as camadas do GeoPackage da Paraíba...")

# --- MODO DETETIVE: Pede pro Python listar o que tem dentro do arquivo ---
camadas = pyogrio.list_layers('municipios_PB.gpkg')
print("🚨 ATENÇÃO! AS CAMADAS REAIS DENTRO DO ARQUIVO SÃO:", camadas)
# -------------------------------------------------------------------------

gdf_poligonos = gpd.read_file('municipios_PB.gpkg', layer='lml_municipio_pb') 
print("1. Carregando as camadas do GeoPackage da Paraíba...")
# Camadas originais
gdf_poligonos = gpd.read_file('municipios_PB.gpkg', layer='lml_municipio_pb')
gdf_poligonos = gdf_poligonos.to_crs(epsg=4326)

gdf_pontos = gpd.read_file('municipios_PB.gpkg', layer='pontos_centroides_municipios')
gdf_pontos = gdf_pontos.to_crs(epsg=4326)

# Novas camadas
gdf_estado = gpd.read_file('municipios_PB.gpkg', layer='lml_estado')
gdf_estado = gdf_estado.to_crs(epsg=4326)

gdf_uc = gpd.read_file('municipios_PB.gpkg', layer='uc_BR')
gdf_uc = gdf_uc.to_crs(epsg=4326)

# Extração de coordenadas dos pontos
gdf_pontos['lat'] = gdf_pontos.geometry.y
gdf_pontos['lon'] = gdf_pontos.geometry.x

print("2. Carregando histórico anterior (Sistema de Checkpoint Inteligente)...")
arquivo_historico = 'historico_risco.csv'
if os.path.exists(arquivo_historico):
    df_historico = pd.read_csv(arquivo_historico)
    historico_dict = df_historico.set_index('nome').to_dict('index')
    print(" -> Histórico encontrado e carregado com sucesso!")
else:
    historico_dict = {}
    print(" -> Primeiro uso: Nenhum histórico anterior encontrado.")

print("3. Buscando dados climáticos na API...")
# Embaralha a ordem das cidades para evitar que as mesmas sofram com eventuais falhas da API
gdf_pontos = gdf_pontos.sample(frac=1).reset_index(drop=True)

gdf_pontos['Umidade_13h'] = 0.0
gdf_pontos['DSC'] = 0
gdf_pontos['Probabilidade_Fogo'] = 0.0
gdf_pontos['Classe_Risco'] = ''
gdf_pontos['Cor_Risco'] = ''
gdf_pontos['Data_Atualizacao'] = ''

total_municipios = len(gdf_pontos)

for index, row in gdf_pontos.iterrows():
    lat = row['lat']
    lon = row['lon']
    nome_cidade = row.get('nome', f'Cidade_{index}')
    
    print(f"Processando [{index + 1}/{total_municipios}]: {nome_cidade}...")
    
    # CACHE INTELIGENTE
    if nome_cidade in historico_dict and historico_dict[nome_cidade].get('Data_Atualizacao') == data_hoje_str:
        print(f"  -> Já atualizado hoje! Usando cache local (Checkpoint).")
        memoria_cidade = historico_dict[nome_cidade]
        gdf_pontos.at[index, 'Umidade_13h'] = memoria_cidade.get('Umidade_13h', np.nan)
        gdf_pontos.at[index, 'DSC'] = memoria_cidade.get('DSC', 0)
        gdf_pontos.at[index, 'Probabilidade_Fogo'] = memoria_cidade.get('Probabilidade_Fogo', 0.0)
        gdf_pontos.at[index, 'Classe_Risco'] = memoria_cidade.get('Classe_Risco', 'Sem Dados')
        gdf_pontos.at[index, 'Cor_Risco'] = memoria_cidade.get('Cor_Risco', '#bdc3c7')
        gdf_pontos.at[index, 'Data_Atualizacao'] = memoria_cidade.get('Data_Atualizacao', 'Dado Antigo')
        continue
    
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=relative_humidity_2m&daily=precipitation_sum&past_days=45&forecast_days=1&timezone=America%2FSao_Paulo"
    
    sucesso = False
    for tentativa in range(3):
        try:
            response = requests.get(url, timeout=(5, 10))
            if response.status_code == 429:
                print(f"  -> Limite da API atingido. Freando bruscamente por 30 segundos...")
                time.sleep(30)
                continue
            response.raise_for_status() 
            dados = response.json()
            sucesso = True
            break
        except Exception as e:
            tempo_espera = (tentativa + 1) * 5
            print(f"  -> Falha de conexão. Aguardando {tempo_espera}s para tentar de novo...")
            time.sleep(tempo_espera)
            
    if sucesso:
        horas = dados['hourly']['time']
        umidades = dados['hourly']['relative_humidity_2m']
        hoje_str_api = agora_br.strftime('%Y-%m-%d') + "T13:00"
        
        if hoje_str_api in horas:
            idx_13h = horas.index(hoje_str_api)
            umidade_hoje = umidades[idx_13h]
        else:
            umidade_hoje = np.nanmean(umidades[-12:-6])
            
        chuvas_diarias = dados['daily']['precipitation_sum']
        chuvas_passado = chuvas_diarias[:-1] 
        
        dsc = 0
        for chuva in reversed(chuvas_passado):
            if chuva is None or chuva <= 2.4:
                dsc += 1
            else:
                break
                
        Z = 1.4285 - (0.1244 * umidade_hoje) + (0.0072 * dsc)
        probabilidade = (1 / (1 + math.exp(-Z))) * 100
        
        if probabilidade < 5.0: classe, cor = '1. Nulo', '#27ae60' 
        elif probabilidade < 10.0: classe, cor = '2. Baixo', '#f1c40f'
        elif probabilidade < 14.0: classe, cor = '3. Moderado', '#e67e22'
        elif probabilidade < 20.0: classe, cor = '4. Alto (Alerta)', '#e74c3c'
        elif probabilidade < 30.0: classe, cor = '5. Muito Alto', '#c0392b'
        else: classe, cor = '6. Crítico', '#8e44ad'

        historico_dict[nome_cidade] = {
            'Umidade_13h': umidade_hoje,
            'DSC': dsc,
            'Probabilidade_Fogo': round(probabilidade, 1),
            'Classe_Risco': classe,
            'Cor_Risco': cor,
            'Data_Atualizacao': data_hoje_str
        }
        
    else:
        print(f"⚠️ API falhou para {nome_cidade}. Buscando na memória...")
        if nome_cidade not in historico_dict:
            print(f"  -> SEM DADOS: A cidade não estava na memória.")
            historico_dict[nome_cidade] = {
                'Umidade_13h': np.nan, 'DSC': 0, 'Probabilidade_Fogo': 0.0,
                'Classe_Risco': 'Sem Dados', 'Cor_Risco': '#bdc3c7', 'Data_Atualizacao': 'Falhou'
            }
        else:
            print(f"  -> SUCESSO: Usando dados antigos.")

    memoria_cidade = historico_dict[nome_cidade]
    gdf_pontos.at[index, 'Umidade_13h'] = memoria_cidade.get('Umidade_13h', np.nan)
    gdf_pontos.at[index, 'DSC'] = memoria_cidade.get('DSC', 0)
    gdf_pontos.at[index, 'Probabilidade_Fogo'] = memoria_cidade.get('Probabilidade_Fogo', 0.0)
    gdf_pontos.at[index, 'Classe_Risco'] = memoria_cidade.get('Classe_Risco', 'Sem Dados')
    gdf_pontos.at[index, 'Cor_Risco'] = memoria_cidade.get('Cor_Risco', '#bdc3c7')
    gdf_pontos.at[index, 'Data_Atualizacao'] = memoria_cidade.get('Data_Atualizacao', 'Dado Antigo')
    
    df_temp_historico = pd.DataFrame.from_dict(historico_dict, orient='index')
    df_temp_historico.index.name = 'nome'
    df_temp_historico.reset_index(inplace=True)
    df_temp_historico.to_csv(arquivo_historico, index=False)
    
    time.sleep(2.0)

print("4. Unindo os resultados matemáticos aos polígonos do mapa...")
colunas_para_levar = ['nome', 'Umidade_13h', 'DSC', 'Probabilidade_Fogo', 'Classe_Risco', 'Cor_Risco', 'Data_Atualizacao']
df_resultados_pontos = gdf_pontos[colunas_para_levar]

gdf_final = gdf_poligonos.merge(df_resultados_pontos, on='nome', how='left')
gdf_final['Cor_Risco'] = gdf_final.get('Cor_Risco', pd.Series(['#bdc3c7']*len(gdf_final))).fillna('#bdc3c7')
gdf_final['Classe_Risco'] = gdf_final.get('Classe_Risco', pd.Series(['Sem Dados']*len(gdf_final))).fillna('Sem Dados')
gdf_final['Data_Atualizacao'] = gdf_final.get('Data_Atualizacao', pd.Series(['Desconhecido']*len(gdf_final))).fillna('Desconhecido')

print("5. Preparando o Mapa Interativo com as Novas Camadas...")
mapa_pb = folium.Map(location=[-7.115, -36.5], zoom_start=7, tiles=None)

# 5.1 Adicionando Mapas Base
folium.TileLayer('cartodbdark_matter', name="Modo Noturno (Padrão)").add_to(mapa_pb)
folium.TileLayer('cartodbpositron', name="Modo Claro").add_to(mapa_pb)
folium.TileLayer('OpenStreetMap', name="Ruas e Satélite (OSM)").add_to(mapa_pb)

# 5.2 Camada de Limite do Estado (Apenas Contorno Fundo)
folium.GeoJson(
    gdf_estado,
    name='Limite Estadual (PB)',
    style_function=lambda x: {
        'color': '#000000', # Contorno Preto
        'weight': 3,        # Borda um pouco mais grossa para destacar
        'fillOpacity': 0    # Interior totalmente transparente
    },
    interactive=False # Desativa cliques na borda do estado
).add_to(mapa_pb)

# 5.3 Camada dos Municípios (Com interatividade)
tooltip_mun = GeoJsonTooltip(
    fields=['nome', 'Probabilidade_Fogo', 'Classe_Risco', 'DSC', 'Umidade_13h', 'Data_Atualizacao'],
    aliases=['Município:', 'Risco de Fogo (%):', 'Classe:', 'Dias Sem Chuva:', 'Umidade às 13h (%):', 'Última Atualização:'],
    localize=True, sticky=False, labels=True,
    style="background-color: #F0EFEF; border: 2px solid black; border-radius: 3px; box-shadow: 3px;"
)

folium.GeoJson(
    gdf_final,
    name='Municípios - Risco de Fogo',
    style_function=lambda feature: {
        'fillColor': feature['properties'].get('Cor_Risco', '#bdc3c7'),
        'color': '#333333', 
        'weight': 1,
        'fillOpacity': 0.75
    },
    highlight_function=lambda feature: {
        'color': '#f1c40f',
        'weight': 4,
        'fillOpacity': 0.9
    },
    tooltip=tooltip_mun
).add_to(mapa_pb)

# 5.4 Camada de Unidades de Conservação (uc_BR) - Apenas Contorno Diferenciado
def estilo_uc(feature):
    esfera = feature['properties'].get('esfera', '')
    # Define as cores baseadas na esfera de gestão
    cor_contorno = '#ffffff' # Branco padrão caso não tenha esfera
    if esfera == 'Federal': cor_contorno = '#e74c3c'      # Vermelho
    elif esfera == 'Estadual': cor_contorno = '#3498db'   # Azul
    elif esfera == 'Municipal': cor_contorno = '#e67e22'  # Laranja
    
    return {
        'color': cor_contorno,
        'weight': 2.5,
        'fillOpacity': 0, # Apenas contorno, nada de preenchimento
        'dashArray': '4, 4' # Faz o traço ser pontilhado para diferenciar das cidades
    }

tooltip_uc = GeoJsonTooltip(
    fields=['nome_uc', 'esfera', 'categoria'],
    aliases=['Unidade de Conservação:', 'Esfera de Gestão:', 'Categoria:'],
    localize=True, sticky=False, labels=True,
    style="background-color: #2c3e50; color: #ecf0f1; border: 1px solid white; border-radius: 3px;"
)

folium.GeoJson(
    gdf_uc,
    name='Unidades de Conservação (UC)',
    style_function=estilo_uc,
    tooltip=tooltip_uc,
    show=True # Deixa ativado por padrão
).add_to(mapa_pb)

# Botão Controlador de Camadas no canto superior direito
folium.LayerControl(collapsed=False).add_to(mapa_pb)
mapa_html = mapa_pb._repr_html_()

print("6. Gerando Ranking do Top 10 e montando Dashboard HTML final...")
top_10 = gdf_final.sort_values(by='Probabilidade_Fogo', ascending=False).head(10)

top_10_display = top_10.rename(columns={
    'nome': 'Município', 
    'Probabilidade_Fogo': 'Risco (%)', 
    'Classe_Risco': 'Classe'
})

tabela_html = top_10_display[['Município', 'Risco (%)', 'Classe']].to_html(
    index=False, 
    classes='table table-striped table-hover table-sm text-start',
    header=True
)

tabela_html = tabela_html.replace('text-align: right;', 'text-align: left;')
tabela_html = tabela_html.replace('6. Crítico', '<span style="color: #8e44ad; font-weight: bold; font-size: 1.1em;">6. Crítico 🚨</span>')
tabela_html = tabela_html.replace('5. Muito Alto', '<span style="color: #c0392b; font-weight: bold;">5. Muito Alto</span>')

pagina_completa = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <title>Painel de Risco - Índice Mata Branca</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body, html {{ height: 100%; margin: 0; padding: 0; }}
        .container-fluid {{ height: 100vh; }}
        .row {{ height: 100%; }}
        .sidebar {{ background-color: #f8f9fa; padding: 20px; overflow-y: auto; height: 100%; border-right: 2px solid #ddd; }}
        .map-container {{ padding: 0; height: 100%; }}
        .table th, .table td {{ text-align: left !important; vertical-align: middle; }}
    </style>
</head>
<body>
    <div class="container-fluid">
        <div class="row">
            <div class="col-md-3 sidebar shadow-sm">
                <h4 class="mb-3 text-danger fw-bold">🔥 Índice Mata Branca</h4>
                <p class="text-muted small mb-4">Atualizado em: {hora_exibicao} (Horário de Brasília)</p>
                <hr>
                <h6 class="fw-bold mb-3 text-dark">⚠️ Top 10 Cidades em Risco</h6>
                {tabela_html}
                <hr>
                <p class="small text-muted mt-3">Metodologia: Equação de Regressão Logística baseada em Umidade Relativa e Dias Sem Chuva (DSC). <br><br><strong>Recomendado para uso tático pelo Corpo de Bombeiros da Paraíba.</strong></p>
                
                <h6 class="fw-bold mt-4 text-dark">🗺️ Legenda Adicional (UCs)</h6>
                <ul class="small text-muted" style="list-style-type: none; padding-left: 0;">
                    <li><span style="color: #e74c3c; font-weight: bold;">---</span> UC Federal</li>
                    <li><span style="color: #3498db; font-weight: bold;">---</span> UC Estadual</li>
                    <li><span style="color: #e67e22; font-weight: bold;">---</span> UC Municipal</li>
                </ul>
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

print("✅ Dashboard gerado com sucesso!")
