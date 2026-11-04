# Argo

ADK agent with a model catalog in `src/configs/models.yaml`.

`src/agent.py` calls `load_model()`. Select a catalog key with `ARGO_MODEL`, or
pass it explicitly to `load_model("gemini-flash-latest")`. Without either, the
first YAML entry is selected (currently Haiku).

```bash
ARGO_MODEL=gemini-flash-latest adk web .
```

Model entries use `type` for the family (`gemini` or `claude`) and `provider`
for the host (`gemini` for the Gemini API, `vertex_ai` for Google Cloud, or
`anthropic` for the direct Claude API). An optional `model` field overrides the
catalog key as the API model ID.

Gemini uses ADK's native `Gemini` adapter. Vertex Claude uses native `Claude`;
direct Anthropic uses `AnthropicLlm`. Neither requires LiteLLM.

For the Gemini API, set `GOOGLE_API_KEY`. For direct Anthropic, set
`ANTHROPIC_API_KEY`. For Vertex, configure ADC with
`gcloud auth application-default login`, enable the model/API and billing, and
set `project` and `location` on the model entry (or use
`GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`). Credentials do not belong
in the model catalog.

For other providers, explicitly set `adapter: litellm` and install the optional
dependency with `uv sync --extra litellm`. The loader constructs the LiteLLM
identifier as `provider/model`. Catalog fields such as `display_name` and
`thinking` describe capabilities; they do not set generation parameters.

Run loader checks without making API calls:

```bash
python -m unittest discover -s tests -v
```
