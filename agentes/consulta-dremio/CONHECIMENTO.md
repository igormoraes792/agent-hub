# Consulta Dremio — conhecimento

## 1. Conexão
- Servidor: `http://172.25.3.16:9047` (Dremio Software). As consultas rodam com o usuário do token (`as.adm`).
- MCP oficial `dremio` (código em `agent-hub\mcp\dremio-mcp`, configuração em `agent-hub\data\dremioai.yaml`).
  Modos ligados: FOR_DATA_PATTERNS + FOR_SELF. `allow_dml: false` — o MCP não cria nem altera views.
- No Dremio, cada consulta do MCP aparece em Jobs como "External Tools", com o SQL começando por `dremioai:`.

## 2. Como consultar
- Descobrir antes de consultar: listar/buscar tabelas, ver o esquema (GetSchemaOfTable) e só então montar o SQL.
- Nunca `SELECT *` em views grandes (ex.: `VENDAS_CONSOLIDADA_DASH`): só colunas específicas, agregações e `LIMIT`.
- Caminhos com espaço vão entre aspas: `GOLD."GRUPO HERVAL".VENDAS."VDS.VENDAS_CONSOLIDADA_DASH"`. As ferramentas de
  esquema e linhagem (`GetSchemaOfTable`, `GetTableOrViewLineage`) usam esse mesmo formato, numa string só.
- Literais só em Latin-1: `→`, `—`, `›` dão "Error during planning". Acentos do português funcionam.
- Datas: usar literais `DATE '2026-10-01'` ou `'2026-10-01'`; timestamp com microssegundos dá
  `GandivaException: Invalid millis for timestamp value`.
- Formatar mês: `SUBSTR(CAST(col AS VARCHAR), 1, 7)`.

## 3. Horários (fuso)
- `CURRENT_TIMESTAMP` e as colunas técnicas (`dh_update_*`, `dh_insert_*`) estão em **UTC**: subtrair 3h e
  informar como horário de Brasília.
- Colunas de negócio (`dt_*`, `dh_captacao`) já estão no horário local: não converter.

## 4. VDS.VENDAS_CONSOLIDADA_DASH
- **Reflection:** consultas que só usam colunas antigas leem uma reflection que pode estar atrasada (~40 min em
  27/10/2026). Colunas `*_faturado` e `dh_update_silver` leem a view ao vivo (~6 s). Para totais confiáveis,
  incluir `MAX(dh_update_silver)` no SELECT para forçar o caminho ao vivo; < 1 s de resposta = reflection.
- **Devolução:** não somar `va_faturado_devolucao` (sinal inconsistente). Devolução =
  `-SUM(CASE WHEN de_tipo_movimento = 'DEVOLUCAO' THEN va_faturado_liquido ELSE 0 END)` (= bruto − líquido).
  Quantidade: mesma lógica com `qt_produto_liquido`.
- Linhas HUB-PEDIDOS têm `va_imposto = 0`.
- Documento: `co_id_order` (= `NU_DOCUMENTO` do Power BI) ou `co_ordem_venda_sap`.
- Centros de lucro iPlace: `20241A`+loja e `20288`+loja = assistência técnica; `202410`+loja = varejo.
  Desde 22/10/2026 ~13:50 a AT vem como 20288; o histórico de 11/09 a 22/10 segue com 20241A.

## 5. Outras views
- `VDS.VENDAS_ITENS_NFCE_CAR`: itens de NFC-e desde 01/09/2024. Cupom = `co_centro` + `serie_nfe` + `nu_nfe`;
  `doc_contabil_sap` é o documento agregado. Vários `COUNT DISTINCT` por cupom no mês podem estourar o tempo.
- O usuário não tem acesso a `sys.reflections`.

## 6. Resposta
- Responder no chat, em tabela quando fizer sentido, citando o SQL usado.
- Não gerar arquivos de resultado (CSV/Excel/relatório) a menos que o usuário peça, e do jeito que ele pedir.
