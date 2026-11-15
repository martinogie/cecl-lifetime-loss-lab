{#
  Where Freddie's rows come from, by environment (kind = 'origination' or 'performance'):
    ci     the tiny synthetic seeds in tests/fixtures (CI never needs real data)
    local  the bronze Parquet written by ingestion/freddie.py, read in place from CECL_BRONZE_PATH
           (e.g. ../data/local/bronze); nothing is copied
    dev / prod  the bronze tables loaded in Databricks
#}
{% macro freddie_relation(kind) -%}
    {%- set folder = {'origination': 'freddie_orig', 'performance': 'freddie_perf'}[kind] -%}
    {%- if target.name == 'local' and env_var('CECL_ENV') == 'ci' -%}
        {{ ref('fixture_freddie_' ~ kind) }}
    {%- elif target.name == 'local' -%}
        read_parquet('{{ env_var("CECL_BRONZE_PATH") }}/{{ folder }}/*/*.parquet', hive_partitioning = true)
    {%- else -%}
        {{ source('bronze', 'freddie_' ~ kind) }}
    {%- endif -%}
{%- endmacro %}
