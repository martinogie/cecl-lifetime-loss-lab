{#
  Use the folder's layer name (silver, gold, fixtures) as the schema, exactly as written. dbt's
  default would prefix the target schema (silver_gold). Environments are kept apart by catalog
  (cecl_lab_dev vs cecl_lab), so the schema names are the same in dev and prod.

  Fallback layout (CECL_DATABRICKS_LAYOUT=schema_prefix, one shared catalog): the schema name
  carries the environment instead: cecl_lab_dev_silver, cecl_lab_silver. Same rule as
  cecl/environment.py databricks_schema().
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{- assert_environment() -}}
    {%- set layer = (custom_schema_name if custom_schema_name is not none else target.schema) | trim | lower -%}
    {%- if target.type == 'databricks' and env_var('CECL_DATABRICKS_LAYOUT', 'catalogs') == 'schema_prefix' -%}
        {{ ('cecl_lab_dev_' if target.name == 'dev' else 'cecl_lab_') ~ layer }}
    {%- else -%}
        {{ layer }}
    {%- endif -%}
{%- endmacro %}
