# Airflow image for the CECL Lifetime Loss Lab. Same base and versions as Bank Health's image, but
# a separate image, separate stacks and separate metadata databases: the two projects never share
# an Airflow instance.
#
# Arm: prod builds this on the shared Oracle VM (aarch64); CI builds linux/amd64 and linux/arm64.
# The base image is multi-arch, and every package here must have an Arm (aarch64) wheel or be pure
# Python: the arm64 CI build is the check. Modelling libraries (LightGBM, SHAP, InterpretML) run on
# Databricks, never in this image.
FROM apache/airflow:3.3.2-python3.12

ARG AIRFLOW_VERSION=3.3.2
ARG PYTHON_VERSION=3.12
ARG CONSTRAINTS=https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt

# 1. Providers and libraries, pinned by Airflow's tested constraints file
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" \
        -r /tmp/requirements.txt --constraint "${CONSTRAINTS}"

# 2. dbt in its own virtualenv so its dependencies can't clash with Airflow's.
#    Exact pair: dbt-databricks 1.12.8 (the newest, Nov 2026) supports dbt-core below 1.12.6, so the
#    image uses dbt-core 1.12.5. Local and CI use dbt-core 1.12.6 with dbt-duckdb
#    (requirements-dbt.txt); move the image to 1.12.6 when dbt-databricks supports it.
RUN python -m venv /home/airflow/dbt_venv \
 && /home/airflow/dbt_venv/bin/pip install --no-cache-dir dbt-core==1.12.5 dbt-databricks==1.12.8

# 3. Startup check: refuses to start unless CECL_ENV is dev or prod
COPY --chmod=755 include/entrypoint.sh /opt/airflow/include/entrypoint.sh

# cecl/ and ingestion/ are mounted at /opt/airflow (docker-compose.yaml), so DAGs import them.
ENV PYTHONPATH=/opt/airflow \
    DBT_EXECUTABLE=/home/airflow/dbt_venv/bin/dbt

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/airflow/include/entrypoint.sh"]
