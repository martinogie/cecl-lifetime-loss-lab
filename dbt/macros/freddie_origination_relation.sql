{#
  Where the origination rows come from: the synthetic seed on the local target (DuckDB, CI), the
  bronze table loaded by the Databricks notebooks on dev and prod.
#}
{% macro freddie_origination_relation() -%}
    {%- if target.name == 'local' -%}
        {{ ref('fixture_freddie_origination') }}
    {%- else -%}
        {{ source('bronze', 'freddie_origination') }}
    {%- endif -%}
{%- endmacro %}
