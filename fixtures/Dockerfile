FROM python:3.15.2-alpine3.24@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd

# The fixture uses only the standard library.
RUN python -m pip uninstall --yes setuptools wheel pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip

WORKDIR /app
COPY --chown=65532:65532 --chmod=0444 fixture_runner.py /app/fixture_runner.py
USER 65532:65532

ENTRYPOINT ["python", "-I", "-B", "/app/fixture_runner.py"]
CMD ["self-test"]
