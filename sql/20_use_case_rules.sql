-- 20_use_case_rules.sql   *** EDIT THIS FILE FOR YOUR TRAFFIC ***
--
-- When a project label isn't mapped in label_map, the use case is inferred from the SHAPE of
-- the traffic only: tokens per call, questions per call, the largest Choice cardinality, and
-- the share of calls made overnight. No customer content is read.
--
-- These thresholds were set on synthetic example data. They will be wrong for real traffic
-- until recalibrated. To recalibrate: run the close, open the UseCases tab, and look at
-- "How reliable is the inferred use case?" -- it scores these rules against every account whose
-- label IS mapped. Adjust thresholds until accuracy is acceptable, then re-run.
--
-- If the usage source has no shape fields, everything unlabelled is 'unclassified'.

CREATE OR REPLACE MACRO infer_use_case(tokens_per_call, questions_per_call, max_cardinality, offhours_share) AS
    CASE
        WHEN tokens_per_call IS NULL OR questions_per_call IS NULL           THEN 'unclassified'
        WHEN tokens_per_call >= 6000                                         THEN 'doc_classification'
        WHEN offhours_share >= 0.5                                           THEN 'data_enrichment'
        WHEN tokens_per_call >= 2000 AND questions_per_call <= 2             THEN 'rag_rerank'
        WHEN tokens_per_call >= 2000                                         THEN 'ci_lint'
        WHEN questions_per_call >= 5                                         THEN 'llm_guardrails'
        WHEN tokens_per_call >= 1300                                         THEN 'fraud_triage'
        WHEN questions_per_call <= 2.5                                       THEN 'model_routing'
        WHEN tokens_per_call <= 750 AND COALESCE(max_cardinality, 0) >= 6    THEN 'moderation'
        WHEN tokens_per_call <= 750                                          THEN 'lead_scoring'
        ELSE 'ticket_routing'
    END;
