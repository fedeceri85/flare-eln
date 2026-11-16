# FLARE AI assistant

`flare_ai` is the optional, removable Django application for FLARE's configurable assistant. It owns
the agent, read-only tools, streamed chat interface, access control, static assets
and tests. The ordinary `lab` application must never import it.

## Enable the assistant

The app is present in the standard image but disabled by default. Configure:

```text
FLARE_AI_ENABLED=1
FLARE_AI_BASE_URL=http://model-server:1234/v1
FLARE_AI_API_KEY=provider-key
FLARE_AI_MODEL=model-identifier
FLARE_AI_GROUP_NAME=FLARE AI users
FLARE_AI_ASSISTANT_NAME=Marcott-E
```

Run the project migrations, create the configured group in Django Admin, and add
approved users to it. Group membership is strict: staff and superusers must also
belong to the group. Disabled users and non-members do not receive the drawer
and cannot call its endpoints.

Provider configuration is deployment-owned in this release. Secrets must stay
in the deployment `.env`, never in source or browser-visible settings.
`FLARE_AI_ASSISTANT_NAME` controls both the displayed chat name and the identity
given to the model; restart the web process after changing it.

## Chat behavior

The authenticated drawer is mounted from the shared FLARE base template. Its
transcript, open state and conversation UUID live in per-tab `sessionStorage`;
authoritative Pydantic message history is bounded to ten completed turns in the
existing Django session. Responses stream as newline-delimited JSON from
`/ai/chat/stream/`. Navigating during a response interrupts that response while
preserving completed messages.

Opening an empty chat sends a hidden greeting to warm the model while the drawer
shows a loading indicator. The completed greeting offers three starter
questions. Reset starts a new conversation and repeats this warm-up.

Only the relative current path is submitted. The server resolves the route and
related record identifiers/names; raw page HTML and unsaved form values are not
sent to the model.

Each explicit user prompt creates an `AI prompt sent` entry in FLARE's existing
read-only Usage Events admin report. The event contains the user, timestamp and
a count of one; it never contains the prompt, response, page context or
conversation ID. Hidden warm-up greetings are not counted.

## Tools

- `search_mice(filters: MouseSearchInput)` performs bounded, typed animal
  searches and returns relative record and combined-summary URLs.
- `search_recordings(filters: RecordingSearchInput)` performs bounded, typed
  recording searches and returns selected-recording and experiment URLs.
- `user_guide(topic="overview")` returns verified static website instructions.

All ORM lookup paths are defined by server code. The model supplies validated
values and never supplies SQL or arbitrary Django lookup expressions. Tools are
read-only.

Direct tool example:

```python
from flare_ai.schemas import MouseSearchInput
from flare_ai.tools.mice import search_mice

print(search_mice(MouseSearchInput(status="Alive", sex="F")))
```

## Tests

```sh
.venv_ai/bin/python manage.py test flare_ai.tests --settings=labdb.test_settings
```

The tests use fake models for the chat transport and do not contact a live AI
provider. Update `user_guide.md` and its topic literal together when FLARE
workflows change.
