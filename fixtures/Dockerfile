FROM python:3.14.9-alpine3.24@sha256:dbeb11d16bb95c3a7e3c25c842cb9f20cbc8c18f8be0461a4a0dcc861ad386a0

# The fixture uses only the standard library.
RUN python -m pip uninstall --yes setuptools wheel pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip

WORKDIR /app
COPY --chown=65532:65532 --chmod=0444 fixture_runner.py /app/fixture_runner.py
USER 65532:65532

ENTRYPOINT ["python", "-I", "-B", "/app/fixture_runner.py"]
CMD ["self-test"]
