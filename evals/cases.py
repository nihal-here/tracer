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
    expected_concrete_implementations={"fastapi_users/authentication/strategy/jwt.py", "fastapi_users/authentication/strategy/redis.py"},
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
    expected_concrete_implementations={"httpx/_transports/default.py", "httpx/_transports/asgi.py"},
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

click_exceptions_case = EvaluationCase(
    id="click-exceptions-001",
    repository_url="https://github.com/pallets/click",
    question="What exception class is raised when a user provides an invalid choice for a choice option, and where is this exception defined and raised?",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Choice Validation",
            alternatives={"src/click/types.py"}
        )
    ],
    expected_concrete_implementations={"src/click/types.py"},
    expected_answer_terms={
        "BadParameter",
        "Choice",
        "convert",
        "fail"
    }
)

drf_oauth_case = EvaluationCase(
    id="drf-oauth-002",
    repository_url="https://github.com/encode/django-rest-framework",
    question="Explain the implementation details of the custom OAuth1 authentication class. In which file is this authentication class defined, and what is its validation logic?",
    expected_evidence_groups=[],
    expected_concrete_implementations=set(),
    expected_answer_terms={
        "not supported",
        "not implemented"
    },
    require_absence_searches=["oauth"],
    require_absence_files=["rest_framework/authentication.py"],
    forbid_citations=True
)

fastapi_dependencies_case = EvaluationCase(
    id="fastapi-dependencies-003",
    repository_url="https://github.com/tiangolo/fastapi",
    question="How does FastAPI resolve and execute dependencies? Trace the execution flow inside fastapi/dependencies/utils.py and identify the helper functions that resolve dependencies, handle generators, and run synchronous dependencies in a thread pool.",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Dependency Resolution",
            alternatives={"fastapi/dependencies/utils.py"}
        )
    ],
    expected_concrete_implementations={"fastapi/dependencies/utils.py"},
    expected_answer_terms={
        "solve_dependencies",
        "solve_generator",
        "run_in_threadpool"
    }
)

pytest_plugins_case = EvaluationCase(
    id="pytest-plugins-004",
    repository_url="https://github.com/pytest-dev/pytest",
    question="How does pytest dynamically register and load external plugins? Which file and class defines the plugin manager, how are setuptools entrypoints discovered, and where does pytest load plugins registered via pytest_plugins?",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Plugin Manager",
            alternatives={"src/_pytest/config/__init__.py"}
        )
    ],
    expected_concrete_implementations={"src/_pytest/config/__init__.py"},
    expected_answer_terms={
        "PytestPluginManager",
        "load_setuptools_entrypoints",
        "consider_module",
        "pytest_plugins",
        "pluggy"
    }
)

cache_reconstruction_case = EvaluationCase(
    id="cache-reconstruction-005",
    repository_url="https://github.com/pallets/click",
    question="What exception class is raised when a user provides an invalid choice for a choice option, and where is this exception defined and raised?",
    expected_evidence_groups=[
        EvidenceGroupRequirement(
            name="Choice Validation",
            alternatives={"src/click/types.py"}
        )
    ],
    expected_concrete_implementations={"src/click/types.py"},
    expected_answer_terms={
        "BadParameter",
        "Choice",
        "convert"
    },
    require_cache_hit=True
)

ALL_CASES = [
    fastapi_users_bearer_case,
    requests_session_case,
    flask_globals_case,
    httpx_transport_case,
    pydantic_types_case,
    click_exceptions_case,
    drf_oauth_case,
    fastapi_dependencies_case,
    pytest_plugins_case,
    cache_reconstruction_case
]

