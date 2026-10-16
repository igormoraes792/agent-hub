-- Painel 212 (dataset 203 "Black Taqi") · canal VAREJO (de_canal (CF)) · setembro/2026 por dt_captacao · por documento
SELECT d.co_id_order,
       MIN(d.dt_captacao)            AS dt_min,
       MAX(d.dt_captacao)            AS dt_max,
       SUM(d.va_faturado_liquido)    AS valor,
       SUM(d.va_faturado_devolucao)  AS devolucao,
       MAX(d.de_tipo_movimento)      AS tipo_movimento,
       MAX(d.de_tipo_pedido)         AS tipo_pedido,
       MAX(d.de_status_pedido)       AS status_pedido,
       MAX(d.co_ordem_venda_sap)     AS ordem_venda_sap,
       MAX(d."de_filial (CF)")       AS filial
FROM (
  SELECT v.*, e_cf.de_filial AS "de_filial (CF)", e_cf.de_canal AS "de_canal (CF)"
  FROM "GOLD.GRUPO HERVAL.VENDAS"."VDS.VENDAS_CONSOLIDADA_DASH" v
  LEFT JOIN (
    SELECT co_empresa, co_centro_lucro, MAX(de_filial) AS de_filial, MAX(de_canal) AS de_canal
    FROM "GOLD.GRUPO HERVAL.DIMENSOES"."VDS.ESTRUTURA_HERVAL"
    GROUP BY co_empresa, co_centro_lucro
  ) e_cf
    ON e_cf.co_empresa = CAST(v.co_empresa AS INTEGER)
   AND e_cf.co_centro_lucro = LTRIM(v.co_centro_lucro_faturado, '0')
  WHERE v.de_negocio = 'TAQI'
) d
WHERE d."de_canal (CF)" = 'VAREJO'
  AND d.dt_captacao >= '2026-09-01' AND d.dt_captacao < '2026-10-01'
GROUP BY d.co_id_order
