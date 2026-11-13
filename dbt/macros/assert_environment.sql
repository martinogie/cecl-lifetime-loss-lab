{#
  Stops dbt unless the target and CECL_ENV belong together (same rules as cecl/environment.py):
    - CECL_ENV must be local, ci, dev or prod (no default)
    - local and ci use the local (DuckDB) target; dev and prod use the target of the same name
    - only prod may build into prod: catalog cecl_lab, or schemas cecl_lab_* in the fallback layout
  Called from generate_schema_name (so it runs while dbt parses, before anything is built) and from
  on-run-start (so it also runs when a cached parse skips the schema macro).
#}
{% macro assert_environment() -%}
    {%- set env = env_var('CECL_ENV', '') -%}
    {%- if env not in ['local', 'ci', 'dev', 'prod'] -%}
        {{ exceptions.raise_compiler_error("CECL_ENV must be local, ci, dev or prod, got '" ~ env ~ "'. There is no default.") }}
    {%- endif -%}
    {%- set expected = 'local' if env in ['local', 'ci'] else env -%}
    {%- if target.name != expected -%}
        {{ exceptions.raise_compiler_error("dbt target '" ~ target.name ~ "' does not match CECL_ENV=" ~ env ~ " (expected target '" ~ expected ~ "').") }}
    {%- endif -%}
    {%- if target.type == 'databricks' -%}
        {%- set catalog = target.database | lower -%}
        {%- if env != 'prod' and catalog == 'cecl_lab' -%}
            {{ exceptions.raise_compiler_error("CECL_ENV=" ~ env ~ " may not build into catalog cecl_lab: only prod writes prod objects.") }}
        {%- endif -%}
        {%- if env == 'prod' and catalog not in ['cecl_lab', 'workspace'] -%}
            {{ exceptions.raise_compiler_error("the prod target must use catalog cecl_lab (or workspace in the fallback layout), not " ~ catalog) }}
        {%- endif -%}
    {%- elif env in ['dev', 'prod'] -%}
        {{ exceptions.raise_compiler_error("CECL_ENV=" ~ env ~ " must use a Databricks target, not " ~ target.type) }}
    {%- endif -%}
{%- endmacro %}

{% macro environment_check_sql() -%}
    {{- assert_environment() -}}
    select 'CECL_ENV={{ env_var("CECL_ENV", "") }} target={{ target.name }}' as environment_check
{%- endmacro %}
