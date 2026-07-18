from evals.schema import EvidenceGroupRequirement, EvaluationCase

fastapi_users_bearer_case = EvaluationCase(
    id="fastapi-users-bearer-001",
    repository_url="https://github.com/fastapi-users/fastapi-users",
    question="Trace a bearer token from the incoming HTTP request until it becomes an authenticated user. Which components extract the token, validate it, and load the user, and what happens when the token is invalid?",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Token Extraction",
            alternatives={"fastapi_users/authentication/transport/bearer.py"}
        ),
        EvidenceGroupRequirement(
            name="Authentication Orchestration",
            alternatives={"fastapi_users/authentication/authenticator.py"}
        ),
        EvidenceGroupRequirement(
            name="Concrete Strategy Validation",
            alternatives={"fastapi_users/authentication/strategy/jwt.py", "fastapi_users/authentication/strategy/redis.py"}
        )
    ],
    expected_answer_terms={
        "BearerTransport",
        "Authenticator",
        "read_token"
    }
)

requests_session_case = EvaluationCase(
    id="requests-session-002",
    repository_url="https://github.com/psf/requests",
    question="How is a Session object defined and initialized? Identify the file where the Session class lives.",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Session Definition",
            alternatives={"requests/sessions.py", "src/requests/sessions.py"}
        )
    ],
    expected_answer_terms={
        "Session",
        "requests"
    }
)

flask_globals_case = EvaluationCase(
    id="flask-globals-003",
    repository_url="https://github.com/pallets/flask",
    question="Where in Flask are the global request and current_app objects defined as proxies, and what underlying library provides the proxy implementation?",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Globals Definition",
            alternatives={"src/flask/globals.py"}
        )
    ],
    expected_answer_terms={
        "LocalProxy",
        "werkzeug"
    }
)

httpx_transport_case = EvaluationCase(
    id="httpx-transport-004",
    repository_url="https://github.com/encode/httpx",
    question="How does the AsyncClient send a request? Trace the request flow from AsyncClient.send down to the transport layer that handles the actual HTTP connection.",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Client Implementation",
            alternatives={"httpx/_client.py"}
        ),
        EvidenceGroupRequirement(
            name="Transport Implementation",
            alternatives={"httpx/_transports/default.py", "httpx/_transports/asgi.py"}
        )
    ],
    expected_answer_terms={
        "AsyncClient",
        "send",
        "handle_async_request"
    }
)

pydantic_types_case = EvaluationCase(
    id="pydantic-types-005",
    repository_url="https://github.com/pydantic/pydantic",
    question="Where does Pydantic define its standard networking types like HttpUrl and AnyUrl?",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Network Types",
            alternatives={"pydantic/networks.py", "src/pydantic/networks.py"}
        )
    ],
    expected_answer_terms={
        "HttpUrl",
        "AnyUrl",
        "networks"
    }
)

ALL_CASES = [
    fastapi_users_bearer_case,
    requests_session_case,
    flask_globals_case,
    httpx_transport_case,
    pydantic_types_case
]
