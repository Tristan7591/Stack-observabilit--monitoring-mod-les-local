FROM python:3.15.2-alpine3.24@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd

RUN python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip \
    && mkdir -p /reports \
    && chown 65532:65532 /reports
WORKDIR /app
COPY --chown=65532:65532 --chmod=0444 main.py engine.py /app/
USER 65532:65532
HEALTHCHECK --interval=20s --timeout=3s --start-period=60s CMD ["python", "-E", "-s", "-B", "/app/main.py", "--health"]
ENTRYPOINT ["python", "-E", "-s", "-B", "/app/main.py"]
