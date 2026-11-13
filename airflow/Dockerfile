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
#    dbt-core matches Bank Health; the dbt-databricks version is confirmed by the first CI build.
RUN python -m venv /home/airflow/dbt_venv \
 && /home/airflow/dbt_venv/bin/pip install --no-cache-dir dbt-core==1.12.6 "dbt-databricks~=1.12.0"

# 3. Startup check: refuses to start unless CECL_ENV is dev or prod
COPY --chmod=755 include/entrypoint.sh /opt/airflow/include/entrypoint.sh

# cecl/ and ingestion/ are mounted at /opt/airflow (docker-compose.yaml), so DAGs import them.
ENV PYTHONPATH=/opt/airflow \
    DBT_EXECUTABLE=/home/airflow/dbt_venv/bin/dbt

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/airflow/include/entrypoint.sh"]
