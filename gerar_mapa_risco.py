import os
import geopandas as gpd
import pandas as pd
import numpy as np
import requests
import math
import time
import folium
import pytz
from folium.features import GeoJsonTooltip
from branca.element import Template, MacroElement
from datetime import datetime

# --- CONFIGURACAO DE TEMPO (FUSO DE BRASILIA) ---
fuso_br = pytz.timezone('America/Sao_Paulo')
agora_br = datetime.now(fuso_br)
data_hoje_str = agora_br.strftime('%d/%m/%Y')
hora_exibicao = agora_br.strftime('%d/%m/%Y %H:%M')

print("1. Carregando as camadas do GeoPackage da Paraiba...")
# Camadas originais
gdf_poligonos = gpd.read_file('dados_espaciais.gpkg', layer='lml_municipio_pb')
gdf_poligonos = gdf_poligonos.to_crs(epsg=4326)

gdf_pontos = gpd.read_file('dados_espaciais.gpkg', layer='pontos_centroides_municipios')
gdf_pontos = gdf_pontos.to_crs(epsg=4326)

# Novas camadas
gdf_estado = gpd.read_file('dados_espaciais.gpkg', layer='lml_estado')
gdf_estado = gdf_estado.to_crs(epsg=4326)

gdf_uc = gpd.read_file('dados_espaciais.gpkg', layer='uc_BR')
gdf_uc = gdf_uc.to_crs(epsg=4326)

# Extracao de coordenadas dos pontos
gdf_pontos['lat'] = gdf_pontos.geometry.y
gdf_pontos['lon'] = gdf_pontos.geometry.x

print("2. Carregando historico anterior (Sistema de Checkpoint Inteligente)...")
arquivo_historico = 'historico_risco.csv'
if os.path.exists(arquivo_historico):
    df_historico = pd.read_csv(arquivo_historico)
    historico_dict = df_historico.set_index('nome').to_dict('index')
    print(" -> Historico encontrado e carregado com sucesso!")
else:
    historico_dict = {}
    print(" -> Primeiro uso: Nenhum historico anterior encontrado.")

print("3. Buscando dados climaticos na API...")
gdf_pontos = gdf_pontos.sample(frac=1).reset_index(drop=True)

# Variaveis atualizadas com o rigor meteorologico (Depressao do Ponto de Orvalho)
gdf_pontos['Depressao_Ponto_Orvalho'] = 0.0
gdf_pontos['DSC_Soares'] = 0.0
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
        print(f"  -> Ja atualizado hoje! Usando cache local (Checkpoint).")
        memoria_cidade = historico_dict[nome_cidade]
        gdf_pontos.at[index, 'Depressao_Ponto_Orvalho'] = memoria_cidade.get('Depressao_Ponto_Orvalho', np.nan)
        gdf_pontos.at[index, 'DSC_Soares'] = memoria_cidade.get('DSC_Soares', 0.0)
        gdf_pontos.at[index, 'Probabilidade_Fogo'] = memoria_cidade.get('Probabilidade_Fogo', 0.0)
        gdf_pontos.at[index, 'Classe_Risco'] = memoria_cidade.get('Classe_Risco', 'Sem Dados')
        gdf_pontos.at[index, 'Cor_Risco'] = memoria_cidade.get('Cor_Risco', '#bdc3c7')
        gdf_pontos.at[index, 'Data_Atualizacao'] = memoria_cidade.get('Data_Atualizacao', 'Dado Antigo')
        continue
    
    # API ATUALIZADA: Buscando Temperatura do Ar e de Orvalho
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,dew_point_2m&daily=precipitation_sum&past_days=45&forecast_days=1&timezone=America%2FSao_Paulo"
    
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
            print(f"  -> Falha de conexao. Aguardando {tempo_espera}s para tentar de novo...")
            time.sleep(tempo_espera)
            
    if sucesso:
        horas = dados['hourly']['time']
        temperaturas = dados['hourly']['temperature_2m']
        orvalhos = dados['hourly']['dew_point_2m']
        hoje_str_api = agora_br.strftime('%Y-%m-%d') + "T13:00"
        
        # Extracao das 13h
        if hoje_str_api in horas:
            idx_13h = horas.index(hoje_str_api)
            temp_hoje = temperaturas[idx_13h]
            orvalho_hoje = orvalhos[idx_13h]
        else:
            temp_hoje = np.nanmean(np.array(temperaturas[-12:-6], dtype=float))
            orvalho_hoje = np.nanmean(np.array(orvalhos[-12:-6], dtype=float))
            
        chuvas_diarias = dados['daily']['precipitation_sum']
        chuvas_passado = chuvas_diarias[:-1] 
        
        # LOGICA DE ABATIMENTO DE SOARES (Cronologico)
        dsc_soares = 0.0
        for chuva in chuvas_passado:
            if chuva is None or chuva <= 2.4: dsc_soares += 1.0
            elif chuva <= 4.9: dsc_soares *= 0.7
            elif chuva <= 9.9: dsc_soares *= 0.4
            elif chuva <= 12.9: dsc_soares *= 0.2
            else: dsc_soares = 0.0
            
        dsc_soares = round(dsc_soares, 2)
        
        if pd.isna(temp_hoje) or pd.isna(orvalho_hoje) or temp_hoje is None or orvalho_hoje is None:
            probabilidade = 0.0
            depressao = np.nan
            classe, cor = 'Sem Dados', '#C7C7C7'
        else:
            depressao = float(temp_hoje) - float(orvalho_hoje)
            
            # FORMULA MATEMATICA VENCEDORA
            Z = -7.3410 + (0.2060 * depressao) + (0.0017 * dsc_soares)
            probabilidade = (1 / (1 + math.exp(-Z))) * 100
            
            # LIMITES OPERACIONAIS OTIMIZADOS
            if probabilidade < 0.4: classe, cor = '1. Nulo', '#A1A1A1' 
            elif probabilidade < 1.9: classe, cor = '2. Baixo', '#C0C276'
            elif probabilidade < 2.9: classe, cor = '3. Moderado', '#E8A523'
            elif probabilidade < 4.8: classe, cor = '4. Alto (Alerta)', '#DE5C0B'
            elif probabilidade < 7.6: classe, cor = '5. Muito Alto', '#DE1010'
            else: classe, cor = '6. Critico', '#82001F'

        historico_dict[nome_cidade] = {
            'Depressao_Ponto_Orvalho': round(depressao, 2) if pd.notna(depressao) else np.nan,
            'DSC_Soares': dsc_soares,
            'Probabilidade_Fogo': round(probabilidade, 1),
            'Classe_Risco': classe,
            'Cor_Risco': cor,
            'Data_Atualizacao': data_hoje_str
        }
        
    else:
        print(f" - API falhou para {nome_cidade}. Buscando na memoria...")
        if nome_cidade not in historico_dict:
            historico_dict[nome_cidade] = {
                'Depressao_Ponto_Orvalho': np.nan, 'DSC_Soares': 0.0, 'Probabilidade_Fogo': 0.0,
                'Classe_Risco': 'Sem Dados', 'Cor_Risco': '#C7C7C7', 'Data_Atualizacao': 'Falhou'
            }

    memoria_cidade = historico_dict[nome_cidade]
    gdf_pontos.at[index, 'Depressao_Ponto_Orvalho'] = memoria_cidade.get('Depressao_Ponto_Orvalho', np.nan)
    gdf_pontos.at[index, 'DSC_Soares'] = memoria_cidade.get('DSC_Soares', 0.0)
    gdf_pontos.at[index, 'Probabilidade_Fogo'] = memoria_cidade.get('Probabilidade_Fogo', 0.0)
    gdf_pontos.at[index, 'Classe_Risco'] = memoria_cidade.get('Classe_Risco', 'Sem Dados')
    gdf_pontos.at[index, 'Cor_Risco'] = memoria_cidade.get('Cor_Risco', '#bdc3c7')
    gdf_pontos.at[index, 'Data_Atualizacao'] = memoria_cidade.get('Data_Atualizacao', 'Dado Antigo')
    
    df_temp_historico = pd.DataFrame.from_dict(historico_dict, orient='index')
    df_temp_historico.index.name = 'nome'
    df_temp_historico.reset_index(inplace=True)
    df_temp_historico.to_csv(arquivo_historico, index=False)
    
    time.sleep(2.0)

print("4. Unindo os resultados matematicos aos poligonos do mapa...")
colunas_para_levar = ['nome', 'Depressao_Ponto_Orvalho', 'DSC_Soares', 'Probabilidade_Fogo', 'Classe_Risco', 'Cor_Risco', 'Data_Atualizacao']
df_resultados_pontos = gdf_pontos[colunas_para_levar]

gdf_final = gdf_poligonos.merge(df_resultados_pontos, on='nome', how='left')
gdf_final['Cor_Risco'] = gdf_final.get('Cor_Risco', pd.Series(['#bdc3c7']*len(gdf_final))).fillna('#bdc3c7')
gdf_final['Classe_Risco'] = gdf_final.get('Classe_Risco', pd.Series(['Sem Dados']*len(gdf_final))).fillna('Sem Dados')
gdf_final['Data_Atualizacao'] = gdf_final.get('Data_Atualizacao', pd.Series(['Desconhecido']*len(gdf_final))).fillna('Desconhecido')

print("5. Preparando o Mapa Interativo com as Novas Camadas...")
mapa_pb = folium.Map(location=[-7.115, -36.5], zoom_start=7, tiles=None)

folium.TileLayer('cartodbdark_matter', name="Modo Noturno (Padrao)").add_to(mapa_pb)
folium.TileLayer('cartodbpositron', name="Modo Claro").add_to(mapa_pb)
folium.TileLayer('OpenStreetMap', name="Ruas e Satelite (OSM)").add_to(mapa_pb)

folium.GeoJson(
    gdf_estado,
    name='Limite Estadual (PB)',
    style_function=lambda x: {
        'color': '#000000', 
        'weight': 3,        
        'fillOpacity': 0    
    },
    interactive=False
).add_to(mapa_pb)

# Tooltip customizado com os nomes rigorosos da meteorologia
tooltip_mun = GeoJsonTooltip(
    fields=['nome', 'Probabilidade_Fogo', 'Classe_Risco', 'DSC_Soares', 'Depressao_Ponto_Orvalho', 'Data_Atualizacao'],
    aliases=['Municipio:', 'Risco de Fogo (%):', 'Classe:', 'DSC (Fator Soares):', 'Depressão do Ponto de Orvalho (C):', 'Ultima Atualizacao:'],
    localize=True, sticky=False, labels=True,
    style="background-color: #F0EFEF; border: 2px solid black; border-radius: 3px; box-shadow: 3px;"
)

folium.GeoJson(
    gdf_final,
    name='Municipios - Risco de Fogo',
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

def estilo_uc(feature):
    esfera = feature['properties'].get('esfera', '')
    cor_contorno = '#ffffff'
    if esfera == 'Federal': cor_contorno = '#005200'
    elif esfera == 'Estadual': cor_contorno = '#1B7A1B'
    elif esfera == 'Municipal': cor_contorno = '#179983'
    
    return {
        'color': cor_contorno,
        'weight': 2.5,
        'fillOpacity': 0 
    }

tooltip_uc = GeoJsonTooltip(
    fields=['nome_uc', 'esfera', 'categoria'],
    aliases=['Unidade de Conservacao:', 'Esfera de Gestao:', 'Categoria:'],
    localize=True, sticky=False, labels=True,
    style="background-color: #2c3e50; color: #ecf0f1; border: 1px solid white; border-radius: 3px;"
)

folium.GeoJson(
    gdf_uc,
    name='Unidades de Conservacao (UC)',
    style_function=estilo_uc,
    tooltip=tooltip_uc,
    show=False 
).add_to(mapa_pb)

legenda_html = '''
{% macro html(this, kwargs) %}
<div style="
    position: fixed; 
    bottom: 30px; left: 30px; width: 200px; height: auto; 
    background-color: rgba(255, 255, 255, 0.95); border: 2px solid #ccc; z-index:9999; font-size:12px;
    padding: 12px; border-radius: 6px; box-shadow: 2px 2px 6px rgba(0,0,0,0.2);
    font-family: Arial, sans-serif;
    ">
    <h6 style="margin-top: 0; font-weight: bold; text-align: center; color: #333;">Classe de Risco</h6>
    <i style="background: #A1A1A1; width: 14px; height: 14px; float: left; margin-right: 8px; border: 1px solid #777;"></i> 1. Nulo<br>
    <i style="background: #C0C276; width: 14px; height: 14px; float: left; margin-right: 8px; border: 1px solid #777; margin-top: 4px;"></i> <span style="display:inline-block; margin-top:4px;">2. Baixo</span><br>
    <i style="background: #E8A523; width: 14px; height: 14px; float: left; margin-right: 8px; border: 1px solid #777; margin-top: 4px;"></i> <span style="display:inline-block; margin-top:4px;">3. Moderado</span><br>
    <i style="background: #DE5C0B; width: 14px; height: 14px; float: left; margin-right: 8px; border: 1px solid #777; margin-top: 4px;"></i> <span style="display:inline-block; margin-top:4px;">4. Alto (Alerta)</span><br>
    <i style="background: #DE1010; width: 14px; height: 14px; float: left; margin-right: 8px; border: 1px solid #777; margin-top: 4px;"></i> <span style="display:inline-block; margin-top:4px;">5. Muito Alto</span><br>
    <i style="background: #82001F; width: 14px; height: 14px; float: left; margin-right: 8px; border: 1px solid #777; margin-top: 4px;"></i> <span style="display:inline-block; margin-top:4px;">6. Critico</span><br>
    
    <hr style="margin: 10px 0; border-top: 1px solid #ccc;">
    
    <h6 style="margin-top: 0; margin-bottom: 8px; font-weight: bold; text-align: center; color: #333;">Unidades de Conservacao</h6>
    <i style="border-top: 3px solid #005200; width: 16px; height: 0; float: left; margin-top: 6px; margin-right: 8px;"></i> Federal<br>
    <i style="border-top: 3px solid #1B7A1B; width: 16px; height: 0; float: left; margin-top: 6px; margin-right: 8px;"></i> <span style="display:inline-block; margin-top:2px;">Estadual</span><br>
    <i style="border-top: 3px solid #179983; width: 16px; height: 0; float: left; margin-top: 6px; margin-right: 8px;"></i> <span style="display:inline-block; margin-top:2px;">Municipal</span><br>
</div>
{% endmacro %}
'''
macro = MacroElement()
macro._template = Template(legenda_html)
mapa_pb.get_root().add_child(macro)

folium.LayerControl(collapsed=False).add_to(mapa_pb)
mapa_html = mapa_pb._repr_html_()

print("6. Gerando Ranking do Top 10 e montando Dashboard HTML final...")
top_10 = gdf_final.sort_values(by='Probabilidade_Fogo', ascending=False).head(10)

top_10_display = top_10.rename(columns={
    'nome': 'Municipio', 
    'Probabilidade_Fogo': 'Risco (%)', 
    'Classe_Risco': 'Classe'
})

tabela_html = top_10_display[['Municipio', 'Risco (%)', 'Classe']].to_html(
    index=False, 
    classes='table table-striped table-hover table-sm text-start',
    header=True
)

tabela_html = tabela_html.replace('text-align: right;', 'text-align: left;')
tabela_html = tabela_html.replace('6. Critico', '<span style="color: #82001F; font-weight: bold; font-size: 1.1em;">6. Critico</span>')
tabela_html = tabela_html.replace('5. Muito Alto', '<span style="color: #DE1010; font-weight: bold;">5. Muito Alto</span>')

# Atualizacao do Corpo do HTML com a metrica da Depressao do Ponto de Orvalho
pagina_completa = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <title>Painel de Risco - Indice Mata Branca</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body, html {{ height: 100%; margin: 0; padding: 0; }}
        .container-fluid {{ height: 100vh; }}
        .row {{ height: 100%; }}
        .sidebar {{ background-color: #f8f9fa; padding: 20px; overflow-y: auto; height: 100%; border-right: 2px solid #ddd; }}
        .map-container {{ padding: 0; height: 100%; position: relative; }}
        .table th, .table td {{ text-align: left !important; vertical-align: middle; }}
    </style>
</head>
<body>
    <div class="container-fluid">
        <div class="row">
            <div class="col-md-3 sidebar shadow-sm">
                <h4 class="mb-3 text-danger fw-bold">Indice Mata Branca</h4>
                <p class="text-muted small mb-4">Atualizado em: {hora_exibicao} (Horario de Brasilia)</p>
                <hr>
                <h6 class="fw-bold mb-3 text-dark">Top 10 Cidades em Risco</h6>
                {tabela_html}
                <hr>
                <h6 class="fw-bold mb-3 text-dark">Modelo Matematico Simplificado (Ockham)</h6>
                <div class="bg-white p-3 border rounded shadow-sm mb-3">
                    <p class="mb-2 text-center" style="font-family: monospace; font-size: 1.1em; color: #333;">
                        <strong>Z = -7.3410 + 0.2060(ΔT) + 0.0017(DSC<sub>S</sub>)</strong><br>
                        <strong>P = 1 / (1 + e<sup>-Z</sup>)</strong>
                    </p>
                    <ul class="small text-muted mb-0 ps-3">
                        <li><strong>ΔT:</strong> Depressão do Ponto de Orvalho às 13h (Temp_Ar - Temp_Orvalho)</li>
                        <li><strong>DSC<sub>S</sub>:</strong> Dias Sem Chuva (metodo de abatimento logistico de Soares)</li>
                        <li><strong>P:</strong> Probabilidade de Ignicao (%)</li>
                    </ul>
                </div>
                <p class="small text-muted mt-3">Metodologia: Equacao de Regressao Logistica treinada especificamente para as dinamicas termodinamicas do semiarido.<br><br><strong>Recomendado para uso tatico pelas forcas de Defesa Civil.</strong></p>
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

print("Dashboard gerado com sucesso!")
