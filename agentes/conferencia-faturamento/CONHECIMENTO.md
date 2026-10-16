# Conferência de faturamento: Power BI × Superset

Manual usado pelos agentes **Conferente de Faturamento**, **Extrator Power BI** e **Extrator Superset**.
Edite este arquivo para ensinar regras novas aos três de uma vez.

## 1. Fluxo

1. O Conferente coleta: **canal, negócio, período, indicador(es) e ID do painel do Superset**.
2. Cria a **área de trabalho temporária**: `python -X utf8 ferramentas/area_trabalho.py criar <negocio>_<canal>_<periodo>`
   (imprime o caminho, em `%TEMP%\agent-hub\conferencias\...`). É a `<pasta>` usada em todo o resto.
3. Chama os dois extratores **ao mesmo tempo**, passando os filtros e a `<pasta>`. Cada um grava um CSV **por documento** nela.
4. Roda `ferramentas/comparar.py` (seção 5) com saída na `<pasta>`.
5. Investiga as diferenças (seção 6) até explicar pelo menos 95% do valor.
6. Entrega o relatório para o Vô **na conversa** (seção 7).

Regra de ouro: **dados grandes nunca passam pela conversa**. Consultas vão para arquivos `.dax`/`.sql` na `<pasta>` e são
executadas pelos scripts. Pela conversa passam só resumos e amostras (até ~50 linhas).

### Arquivos: só quando o usuário pedir
- Tudo o que é intermediário (consultas, CSVs, resumos, scripts de análise) fica **somente na área temporária**, que se apaga sozinha
  depois de 2 dias. **Nunca** grave nada nesta pasta do agente nem em outras pastas do usuário por conta própria.
- **Não gere relatórios, planilhas ou CSVs para o usuário** a menos que ele peça. Quando pedir, gere **exatamente** no formato
  (Excel, CSV, Markdown, PDF…) e no local que ele indicar; se ele não disser onde, pergunte. Para Excel, use `divergencias.csv`
  da área temporária como base.
- Modelos de consulta já testados ficam em `modelos/` (somente leitura: copie para a `<pasta>` antes de adaptar).

## 2. Ferramentas (rodar a partir desta pasta, com `python -X utf8`)

| Script | O que faz |
|---|---|
| `ferramentas/extrair_pbi.py --dax arq.dax --saida arq.csv` | Roda DAX no modelo publicado e grava CSV (até 100 mil linhas / 1 milhão de valores) |
| `ferramentas/consultar_pbi.py --dax "EVALUATE ..."` (ou `--arquivo arq.dax`) | Consulta rápida ao Power BI: validações e investigação de poucos documentos |
| `ferramentas/extrair_superset.py --sql arq.sql --chave co_id_order --saida arq.csv` | Roda SQL no Superset (Dremio) paginando de 1000 em 1000 até trazer tudo |
| `ferramentas/comparar.py ...` | Cruza os dois CSVs por documento e gera na `<pasta>`: `resumo.md`, `resumo.json`, `divergencias.csv`, `por_dia.csv` |
| `ferramentas/area_trabalho.py criar <rótulo>` | Cria a área de trabalho temporária da conferência e limpa as com mais de 2 dias |

**Power BI: use SEMPRE os scripts acima, nunca as ferramentas MCP do Power BI.** Os scripts usam a API REST com o login
guardado pelo Azure CLI (sem janela de login); as ferramentas MCP do Power BI abrem login da Microsoft a cada uso.
Se um script disser que o acesso falhou, peça ao usuário para rodar `az login` uma vez.
**Superset:** para consultas pequenas de investigação (poucos documentos) pode usar as ferramentas MCP do Superset direto.

## 3. Power BI (fonte FIXA)

- Workspace: **0. Workspace PPU c/recursos especiais**
- Modelo: **60000053 - Faturamento Negocios Global** (publicado; não precisa do arquivo aberto)
- Acesso: API REST `executeQueries` com o login do Azure CLI (conta pb.corporativo@herval.com.br, admin do workspace).
  Os scripts `extrair_pbi.py` e `consultar_pbi.py` já fazem tudo; os IDs do workspace e do modelo ficam em cache.

### Tabelas e colunas
- Fatos: `VW_DW_ODS_GLOBAL_PEDIDO`
  - Documento: `NU_DOCUMENTO` (texto). Outras chaves: `NU_ORDEM_VENDA`, `NU_ORDEM_DEVOLUCAO`, `NU_NOTA`, `NU_PEDIDO`
  - Valores: `VA_FAT_LIQUIDO`, `VA_FAT_BRUTO`, `VA_FAT_DEVOLVIDO` (negativo), `QT_LIQUIDO`, `QT_BRUTO`, `QT_DEVOLVIDO`
  - Datas: `SK_TEMPO` (AAAAMMDD, data de **faturamento**), `DATA_VENDA`
  - Outros: `DE_TIPO_PEDIDO`, `CO_TIPO_ORDEM_VENDA`, `NO_MARKETPLACE`, `CO_CANAL_DISTRIBUICAO`, `CO_TIPO_FATURAMENTO`
- Estrutura: `VW_DW_DIM_ESTRUTURA_HERVAL_UN[DE_NEGOCIO]` e `[DE_CANAL]`
- Tempo: `DIM_TEMPO_P1[DT_DATA]`, `[NU_ANO]`, `[NU_MES]`, `[SK_TEMPO]`.
  **A ligação DIM_TEMPO_P1 ↔ fatos é inativa**: use `USERELATIONSHIP(DIM_TEMPO_P1[SK_TEMPO], VW_DW_ODS_GLOBAL_PEDIDO[SK_TEMPO])`
  em todo cálculo direto sobre colunas. As medidas oficiais já fazem isso internamente.
- Seleção do Varejo: `'_GC - VAREJO - VA+L'[SELECAO_VAREJO_VA+L]` = `"Próprio"` (padrão do relatório),
  `"Representante"` ou `"Próprio+V. Assistida+ Retira loja (Varejo)"`. **Sempre filtre explicitamente**; sem pedido em contrário, use "Próprio".

### Valores aceitos
- Negócios: `TAQI`, `IPLACE`, `HT`, `OUTROS NEGÓCIOS`
- Canais: `VAREJO`, `ECOMMERCE`, `CORPORATIVO`, `INTERCOMPANY`

### Indicadores (use SEMPRE a medida oficial: ela respeita a seleção "Próprio")
| Indicador | Medida |
|---|---|
| Faturamento líquido | `[P1 Vlr Faturamento Líquido]` |
| Faturamento bruto | `[P1 Vlr Faturamento Bruto]` |
| Devoluções | `[P1 Vlr Faturamento Devoluções]` |
| Quantidade líquida | `[P1 Qtd Faturamento Líquido]` |

### Modelo de consulta por documento (padrão testado)
Primeiro a base com a medida (só entram documentos com valor no recorte); depois os atributos.
**Não coloque atributos dentro do SUMMARIZECOLUMNS**: eles não respeitam negócio/canal e trazem documentos de outros negócios.
```dax
DEFINE
    VAR DataIni = DATE(2026, 9, 1)
    VAR DataFim = DATE(2026, 9, 30)
    VAR Base =
        SUMMARIZECOLUMNS(
            VW_DW_ODS_GLOBAL_PEDIDO[NU_DOCUMENTO],
            TREATAS({"Próprio"}, '_GC - VAREJO - VA+L'[SELECAO_VAREJO_VA+L]),
            TREATAS({"TAQI"}, VW_DW_DIM_ESTRUTURA_HERVAL_UN[DE_NEGOCIO]),
            TREATAS({"VAREJO"}, VW_DW_DIM_ESTRUTURA_HERVAL_UN[DE_CANAL]),
            FILTER(ALL(DIM_TEMPO_P1[DT_DATA]), DIM_TEMPO_P1[DT_DATA] >= DataIni && DIM_TEMPO_P1[DT_DATA] <= DataFim),
            "valor", [P1 Vlr Faturamento Líquido]
        )
EVALUATE
ADDCOLUMNS(
    Base,
    "dt", CALCULATE(MIN(VW_DW_ODS_GLOBAL_PEDIDO[SK_TEMPO]), USERELATIONSHIP(DIM_TEMPO_P1[SK_TEMPO], VW_DW_ODS_GLOBAL_PEDIDO[SK_TEMPO]), DATESBETWEEN(DIM_TEMPO_P1[DT_DATA], DataIni, DataFim)),
    "dt_venda", CALCULATE(MIN(VW_DW_ODS_GLOBAL_PEDIDO[DATA_VENDA]), USERELATIONSHIP(DIM_TEMPO_P1[SK_TEMPO], VW_DW_ODS_GLOBAL_PEDIDO[SK_TEMPO]), DATESBETWEEN(DIM_TEMPO_P1[DT_DATA], DataIni, DataFim)),
    "tipo_pedido", CALCULATE(MAX(VW_DW_ODS_GLOBAL_PEDIDO[DE_TIPO_PEDIDO]), USERELATIONSHIP(DIM_TEMPO_P1[SK_TEMPO], VW_DW_ODS_GLOBAL_PEDIDO[SK_TEMPO]), DATESBETWEEN(DIM_TEMPO_P1[DT_DATA], DataIni, DataFim)),
    "ordem_venda", CALCULATE(MAX(VW_DW_ODS_GLOBAL_PEDIDO[NU_ORDEM_VENDA]), USERELATIONSHIP(DIM_TEMPO_P1[SK_TEMPO], VW_DW_ODS_GLOBAL_PEDIDO[SK_TEMPO]), DATESBETWEEN(DIM_TEMPO_P1[DT_DATA], DataIni, DataFim)),
    "devolvido", CALCULATE(SUM(VW_DW_ODS_GLOBAL_PEDIDO[VA_FAT_DEVOLVIDO]), USERELATIONSHIP(DIM_TEMPO_P1[SK_TEMPO], VW_DW_ODS_GLOBAL_PEDIDO[SK_TEMPO]), DATESBETWEEN(DIM_TEMPO_P1[DT_DATA], DataIni, DataFim))
)
```
Validação obrigatória: a soma de `valor` no CSV deve bater com a medida total do mesmo recorte
(`python -X utf8 ferramentas/consultar_pbi.py --dax "EVALUATE ROW(\"total\", CALCULATE([medida], filtros...))"`).
Exemplo de referência: TAQI · VAREJO · Próprio · set/2026 = **12.540.284,33** em **16.613** documentos.

### Atualização
O modelo publicado só tem dados até a última atualização agendada. Antes de comparar, verifique
`MAX(ATUALIZACAO_FLUXO_VENDAS[ATUALIZACAO_FLUXO])` e o maior `SK_TEMPO` do recorte. Dias após a última carga **não podem**
ser comparados: avise e exclua do período.

## 4. Superset (painel informado pelo ID)

Descobrir como o painel calcula o indicador, e reproduzir **a mesma regra** por documento:
1. `superset_dashboard_get(ID)` e `superset_dashboard_charts(ID)`: identifique o(s) gráfico(s) do indicador (KPI/big number/tabela)
   e a expressão da métrica (ex.: `SUM(va_faturado_liquido)`).
2. `superset_dashboard_datasets(ID)`: pegue o **SQL do dataset** (datasets virtuais trazem JOINs e WHERE próprios) e o `database_id`.
3. `superset_dashboard_filter_list(ID)`: veja **qual coluna** cada filtro nativo usa (período, canal, loja…).
   Ex.: no painel 212 o período usa `dt_captacao` (data de **captação**) e o canal usa `"de_canal (CF)"`.
4. Monte o SQL por documento envolvendo o SQL do dataset como subconsulta, aplicando os filtros nas MESMAS colunas do painel,
   com `GROUP BY` na chave e **sem ORDER BY/LIMIT** (o script pagina sozinho).
5. Valide: a soma do CSV deve bater com o número do KPI do painel no mesmo recorte (rode a métrica agregada uma vez).

Chave de documento: `co_id_order` (= `NU_DOCUMENTO` do Power BI). Outras chaves úteis: `co_ordem_venda_sap` (zeros à esquerda),
`nu_pedido`, `co_id_pedido_marketplace`.

### Painel 212 (Black Friday - Taqi), referência
- Dataset 203 "Black Taqi", banco 3 (Dremio). Tabela base `"GOLD.GRUPO HERVAL.VENDAS"."VDS.VENDAS_CONSOLIDADA_DASH"`,
  com JOIN em `"GOLD.GRUPO HERVAL.DIMENSOES"."VDS.ESTRUTURA_HERVAL"` pelo centro de lucro **faturado** (gera `"de_canal (CF)"`, `"de_filial (CF)"`).
  O dataset já filtra `de_negocio = 'TAQI'` e o ano de 2026.
- Métrica faturamento: `SUM(va_faturado_liquido)`. Período: `dt_captacao`. Canal: `"de_canal (CF)"`.
- Exemplo: VAREJO · set/2026 = **12.784.493,32** em **16.175** documentos.
- Modelos testados: `modelos/superset_painel_212_por_documento.sql` e `modelos/pbi_por_documento.dax`.

## 5. Comparação

```
python -X utf8 ferramentas/comparar.py --pbi <pasta>/pbi.csv --superset <pasta>/superset.csv ^
  --chave-pbi NU_DOCUMENTO --chave-ss co_id_order --valor-pbi valor --valor-ss valor ^
  --data-pbi dt --data-ss dt_min --saida <pasta>
```
Categorias: Igual · Valor diferente · Só no Power BI · Só no Superset. A soma das diferenças das categorias = diferença total.

## 6. Investigação: causas conhecidas e como confirmar

Para cada categoria, pegue os maiores documentos e uma amostra, consulte-os **nos dois sistemas sem filtro de período/canal**
e classifique em uma das causas abaixo. Quantifique cada causa (soma e nº de docs) até explicar ≥ 95% da diferença.

| Causa | Sinal típico | Como confirmar |
|---|---|---|
| **Data diferente** (PBI = data de faturamento; painel = data de captação) | Doc só de um lado, perto da virada do mês; dias sem venda num lado (domingos/feriados) | Ver `SK_TEMPO`/`DATA_VENDA` no PBI e `dt_captacao`/`dh_captacao` no Superset do mesmo doc |
| **Devolução em mês diferente** | Valores negativos só no PBI (devolução de venda de mês anterior); doc com valor 0 no PBI e positivo no Superset (vendido e devolvido) | PBI: `VA_FAT_DEVOLVIDO`, `NU_ORDEM_DEVOLUCAO`. Superset: linhas com `de_tipo_movimento = 'DEVOLUCAO'`, `va_faturado_devolucao` e a data delas |
| **Pedido ausente no Dremio** (falha de carga) | Doc com venda normal só no PBI e inexistente no Superset em qualquer data | Buscar o doc no Superset sem filtros; ver buracos por hora (`EXTRACT(HOUR FROM dh_captacao)`) e `dh_update_silver`/`dh_update_iceberg` |
| **Faturamento parcial** | Mesmo doc e data, valores diferentes | Comparar itens/quantidades do doc nos dois lados |
| **Chave diferente** (marketplace/ecommerce, códigos com prefixo como `p15292543`) | Doc só de um lado com formato de código diferente | Procurar pelo valor/data nas chaves `co_ordem_venda_sap`, `nu_pedido`, `co_id_pedido_marketplace` |
| **Canal/loja diferente** | Doc existe nos dois, mas fora do filtro em um deles | Consultar o doc sem filtro de canal; comparar estrutura (centro de lucro faturado × loja) |
| **Atualização** | Diferença concentrada nos últimos dias | Ver última carga de cada lado (seção 3 e `MAX(dh_captacao)`) |

Se algo não se encaixar, descreva o padrão com exemplos; não force uma causa.

## 7. Relatório para o Vô

Objetivo: o Vô conseguir **corrigir** a origem. Estrutura:
1. **Recorte** (negócio, canal, período, indicador, painel) e última atualização de cada lado.
2. **Totais**: Power BI × Superset × diferença (valor e nº de documentos).
3. **Diferença explicada por causa**: tabela causa → docs → valor → % da diferença → ação sugerida.
4. **Detalhe por causa**: até 10 documentos-exemplo cada (documento, datas nos dois lados, valores, o que aconteceu).
5. **Não explicado**: o que sobrou, com exemplos.
6. **Fechamento**: uma linha oferecendo exportar (ex.: "Posso gerar a lista completa dos 834 documentos em Excel, se quiser").
   Não gere nenhum arquivo sem o pedido do usuário (seção 1).
O relatório vai **na conversa**. Valores em R$ no formato brasileiro (1.234,56). Sem jargão técnico além do necessário.
