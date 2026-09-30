# PASE Studio

Editor desktop visual para configurar e executar simulações PASE sem terminal.

## Como abrir

Use `..\Abrir PASE.vbs`. O iniciador prepara o ambiente Python, instala a
interface quando necessário e abre apenas a janela do aplicativo.

## Instalacao em um computador Windows novo

Baixe `Instalar PASE.bat` da raiz do repositório no GitHub e execute-o. O
instalador prepara Git, Miniforge/Python, Node.js, o ambiente científico, o
submódulo pySTICS e a interface Electron; ao final, cria `PASE Studio.bat` na
Área de Trabalho. Não requer permissões de administrador. Requer Windows 10/11
de 64 bits e conexão com a internet.

O instalador nao sobrescreve uma copia anterior: se `PASE\agro` ja existir,
cria uma nova pasta com data/hora no nome. SIMPLE e GRASSIM usam as dependências
instaladas. STICS ainda exige o executável JavaSticsCmd, que não é distribuído
neste repositorio.

## Fontes e proveniência

- OpenStreetMap/Nominatim: busca de lugares e endereços.
- Open-Meteo/Copernicus DEM: altitude, fuso e condições meteorológicas.
- PVGIS: séries históricas usadas pelo motor PASE.
- pvlib CEC: catálogo de módulos e inversores.

As respostas externas ficam em `.data/cache.sqlite3`. A interface mostra a
fonte e se a resposta veio da rede, cache atual ou cache antigo.

## Modos de cálculo

O botão **Otimizar configuração** usa uma aproximação rápida multiobjetivo para
manter a edição interativa. O resultado é sempre identificado como candidato.
O botão **Simular** cria uma pasta isolada em `SIMULACOES/<nome>/runs/<id>`,
gera os inputs correspondentes e executa o motor PASE completo.

Projetos salvos usam a extensão `.pase-project` e o esquema `schemaVersion: 1`.
Chaves Google opcionais são criptografadas pelo Windows/Electron e nunca são
gravadas dentro do projeto.

## Desenvolvimento

```powershell
cd studio
npm install
npm run build
```

Testes do serviço:

```powershell
python -m unittest discover -s studio/tests -v
```
