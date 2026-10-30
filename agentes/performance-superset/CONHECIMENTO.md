# Performance Superset — conhecimento

Objetivo: dizer quanto tempo cada gráfico de um painel leva até aparecer na tela, por quê, e o que mudar
(no gráfico, no dataset ou na view do Dremio) para ficar mais rápido. **Só analisar e sugerir:** nunca alterar
gráficos, datasets, painéis ou views. Se o usuário pedir uma correção, explique o que mudar e onde; não aplique.

## 1. Fluxo
1. Rodar a medição (leva de 3 a 6 min num painel com ~15 gráficos):
   `cd "<pasta do agente>"; python -X utf8 ferramentas/medir_painel.py --painel <id>`
   - O JSON sai no stdout (o progresso vai para o stderr). Não gravar em arquivo, a menos que o usuário peça.
   - Opções: `--dias-log 7` (janela do log), `--graficos 2597,2598` (só alguns),
     `--sem dremio,concorrencia,filtros,log,medicao` (pular etapas; ex.: só o log = `--sem medicao,filtros`).
2. Se preciso, aprofundar: SQL dos datasets (no JSON), MCP `superset-herval` (só leitura) e MCP `dremio`
   (linhagem, esquema da view: `GetTableOrViewLineage`, `GetSchemaOfTable`). Nome no formato SQL do Dremio, numa
   string só: `GOLD."GRUPO HERVAL".VENDAS."VDS.VENDAS_CONSOLIDADA_DASH"` (outros formatos dão erro).
3. Responder no formato da seção 5.

## 2. O que cada parte do JSON mede
- `estrutura`: abas (só a primeira aba de cada grupo carrega na abertura: `carrega_na_abertura`), filtros nativos
  (valor padrão, dependências em cascata), datasets (virtual?, cache, alertas no SQL).
- `graficos.<id>.sem_cache`: o gráfico sozinho, forçando o Superset a consultar o Dremio (force). Inclui o valor
  padrão dos filtros nativos (`filtros_aplicados`), como na abertura real. `kb` = tamanho da resposta, `linhas`.
- `graficos.<id>.com_cache`: a mesma consulta de novo. Se `do_cache` = false, o cache está desligado (-1) e toda
  abertura paga o tempo cheio. `cache_timeout_efetivo` mostra o cache realmente usado (gráfico > dataset > banco).
- `graficos.<id>.dremio_direto`: o SQL gerado pelo Superset rodado direto no Dremio (API REST):
  - `segundos_no_dremio` = execução no Dremio. A diferença para `sem_cache.segundos` é custo do Superset
    (fila de workers, conexão Flight, serialização) + rede.
  - `reflections_usadas` (CHOSEN) e `reflections_casadas_nao_usadas` (MATCHED): se a consulta foi acelerada.
    Se não aparece nenhuma reflection da view consultada, ela leu ao vivo.
- `concorrencia`: os gráficos da abertura rodando juntos (6 por vez, como o navegador). `total_s` é o mais perto
  do "painel pronto" sem cache; compare com `soma_individual_s`. Se cada consulta fica bem mais lenta junto do que
  sozinha, o gargalo é o Dremio/Superset sob carga, não o gráfico.
- `consultas_dos_filtros`: consulta que preenche cada filtro de lista. Regra da casa: filtro nunca consulta a view
  de vendas; usa dataset de dimensão (ex.: 326/327 no painel 212).
- `log_usuarios`: o que os usuários realmente viveram (eventos do navegador gravados no log do Superset).
  - "carga" = gráficos disparados pela mesma ação; `aberturas` = primeira carga de cada aba do navegador;
    `outras_cargas` = filtros/atualizações.
  - `abertura_completa_*` = do primeiro pedido até o último gráfico desenhado.
  - por gráfico: `consulta_*` (pedido até a resposta, inclui a fila do navegador), `desenho_mediana_s`
    (render no navegador), `pct_do_cache`, `erros`, `estouros_60s` (consultas cortadas no limite de ~60 s do
    Superset: o usuário vê erro em vez do gráfico).
  - `abertura_por_hora_brasilia`: horários de pico. Os horários do log vêm em UTC; o script já converte.
  - `force_refresh_dashboard` alto nos tipos de evento = usuários apertando "atualizar" (ou painel lento).

## 3. Causas comuns e sugestões (em ordem de ganho típico)
1. **Cache desligado (-1)** no dataset/gráfico: toda abertura consulta o Dremio. Sugerir cache curto (ex.: 120 s)
   para painéis de acompanhamento. `null` herda o cache do banco "Dremio" (21600 s = 6 h), longo demais para
   painel do dia. Exceção conhecida: dataset 256 ("Última atualização") fica em -1 por decisão do usuário.
2. **Estouros de 60 s / fila no pico**: muitos gráficos pesados ao mesmo tempo. Reduzir consultas (item 4),
   ligar cache, aquecer cache (`chart_cache_warmup`) antes do pico, mover gráficos pesados para outra aba.
3. **Dataset virtual pesado**: JOIN, `SELECT *`, função na chave do JOIN, `COUNT DISTINCT`, `ORDER BY`, `UNION`.
   O Superset embrulha o SQL do dataset como subconsulta em cada gráfico, então o custo se repete N vezes.
   Sugerir levar a lógica para a view no Dremio (feita uma vez, pode ter reflection) ou usar colunas prontas
   (ex.: `*_faturado` em vez de JOIN com ESTRUTURA_HERVAL).
4. **Consultas repetidas**: vários gráficos com o mesmo dataset e filtros, mudando só a métrica, podem virar um só
   (ex.: um Handlebars com vários indicadores) — menos consultas concorrentes.
5. **Reflection**: se `dremio_direto` não mostra reflection da view e o tempo no Dremio é alto, sugerir reflection
   (agregada, com as colunas/dimensões usadas) ao responsável pela view, ou evitar colunas que tiram a consulta
   da reflection. Na `VENDAS_CONSOLIDADA_DASH`, `*_faturado` e `dh_update_silver` já tiraram consultas da
   reflection; confirmar sempre pelo `dremio_direto`. O usuário não acessa `sys.reflections` nem `sys.jobs`.
6. **Volume para o navegador**: `row_limit` alto, tabela raw sem paginação no servidor, Handlebars com centenas
   de linhas, muitas séries. Ver `kb`, `linhas` e `desenho_mediana_s`.
7. **Filtros nativos**: filtro de lista sobre a view de vendas, cascatas longas, "selecionar primeiro valor"
   (segura os gráficos até a lista carregar), filtro sem a coluna no dataset do gráfico (é ignorado).
8. **Abas**: gráficos fora da primeira aba não pesam na abertura; mover os pesados e menos usados para lá.
9. **Atualização automática** curta + cache desligado = carga contínua no Dremio.

## 4. Cuidados
- A medição roda as consultas de verdade (sem cache) e soma carga ao Dremio: evitar repetir em sequência e
  avisar se o painel for muito grande (> 30 gráficos) antes de rodar tudo.
- Os tempos variam com o horário (fila do Dremio). Sempre informar a hora da medição (horário de Brasília).
- `com_cache` mais lento que `sem_cache` com `do_cache` = false não é erro: sem cache, a segunda rodada é outra
  consulta completa e pega outra carga do Dremio.
- O log traz nomes de usuários: relatar só agregados (quantos usuários), nunca nomes.
- Fuso: horários técnicos (log do Superset, `changed_on`, `dh_update_*`) vêm em UTC; informar em Brasília.

## 5. Formato da resposta (no chat)
1. **Resumo**: tempo típico de abertura (log: mediana e p90), % de estouros, % de cache, painel completo sem
   cache (concorrência), hora da medição.
2. **Tabela por gráfico**: ID, nome, tipo, aba, sozinho sem cache (s), com cache (s), no Dremio (s), reflection
   (sim/não), linhas, KB, log: mediana / p90 / estouros, alertas.
3. **Gargalos**, do maior para o menor, com a evidência (números do JSON).
4. **Sugestões priorizadas**: o que mudar, onde (gráfico / dataset / view), ganho estimado e esforço.
   Separar o que o usuário faz no Superset do que depende do responsável pela view no Dremio.
5. Não gerar arquivos (CSV/relatório) a menos que o usuário peça, e do jeito que ele pedir.
