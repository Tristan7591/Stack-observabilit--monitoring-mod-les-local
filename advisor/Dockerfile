FROM python:3.14.9-alpine3.24@sha256:dbeb11d16bb95c3a7e3c25c842cb9f20cbc8c18f8be0461a4a0dcc861ad386a0

RUN python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip \
    && mkdir -p /reports \
    && chown 65532:65532 /reports
WORKDIR /app
COPY --chown=65532:65532 --chmod=0444 main.py engine.py /app/
USER 65532:65532
HEALTHCHECK --interval=20s --timeout=3s --start-period=60s CMD ["python", "-E", "-s", "-B", "/app/main.py", "--health"]
ENTRYPOINT ["python", "-E", "-s", "-B", "/app/main.py"]
