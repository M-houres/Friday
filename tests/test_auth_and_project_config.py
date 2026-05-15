import asyncio
import json


def test_password_hash_and_verify():
    from src.api.auth import hash_password, verify_password

    password_hash, salt = hash_password("Password123")

    assert password_hash
    assert salt
    assert verify_password("Password123", salt, password_hash) is True
    assert verify_password("Wrong12345", salt, password_hash) is False


def test_issue_and_decode_access_token():
    from src.api.auth import decode_jwt, issue_access_token, resolve_jwt_secret
    from src.config import settings

    token = issue_access_token(
        user_id="u-1",
        email="user@example.com",
        roles=["builder"],
        name="User",
        expires_in_seconds=3600,
    )
    payload = decode_jwt(token, resolve_jwt_secret(), settings.jwt_algorithm)

    assert payload["sub"] == "u-1"
    assert payload["email"] == "user@example.com"
    assert payload["roles"] == ["builder"]


def test_resolve_jwt_secret_is_not_static_in_dev(monkeypatch):
    from src.api.auth import resolve_jwt_secret

    monkeypatch.setattr("src.api.auth.settings.environment", "dev")
    monkeypatch.setattr("src.api.auth.settings.jwt_secret", "")

    secret = resolve_jwt_secret()

    assert secret
    assert secret != "friday-dev-secret"


def test_auth_public_paths_cover_register_and_login():
    from src.api.auth import is_public_path

    assert is_public_path("/api/v1/auth/register") is True
    assert is_public_path("/api/v1/auth/login") is True


def test_project_config_store_save_and_page_crud(tmp_path):
    from src.productization.project_config_store import ProjectConfigStore

    store = ProjectConfigStore(config_dir=tmp_path)
    project = store.save_project(
        {
            "id": "demo",
            "name": "Demo",
            "description": "demo project",
            "skills": ["SkillA"],
            "pages": [
                {
                    "id": "home",
                    "name": "Home",
                    "route": "/",
                    "page": "home.html",
                    "skills": ["SkillA"],
                    "is_home": True,
                }
            ],
        }
    )

    assert project["id"] == "demo"
    assert project["home_route"] == "/"

    project = store.upsert_page(
        "demo",
        {
            "id": "legal",
            "name": "Legal",
            "route": "/legal",
            "page": "legal.html",
            "skills": ["SkillB"],
            "billing": {"required": True, "credits_cost": 9},
        },
    )
    assert len(project["pages"]) == 2
    assert any(page["id"] == "legal" for page in project["pages"])

    project = store.delete_page("demo", "legal")
    assert project is not None
    assert all(page["id"] != "legal" for page in project["pages"])

    payload = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert payload["id"] == "demo"
    assert payload["pages"][0]["id"] == "home"


def test_project_config_store_save_project_keeps_pages_when_not_provided(tmp_path):
    from src.productization.project_config_store import ProjectConfigStore

    store = ProjectConfigStore(config_dir=tmp_path)
    store.save_project(
        {
            "id": "demo",
            "name": "Demo",
            "pages": [{"id": "home", "name": "Home", "route": "/", "page": "home.html", "is_home": True}],
        }
    )

    updated = store.save_project(
        {
            "id": "demo",
            "name": "Demo v2",
            "description": "updated",
        }
    )

    assert updated["name"] == "Demo v2"
    assert len(updated["pages"]) == 1
    assert updated["pages"][0]["id"] == "home"


def test_project_config_store_delete_home_page_recomputes_home_route(tmp_path):
    from src.productization.project_config_store import ProjectConfigStore

    store = ProjectConfigStore(config_dir=tmp_path)
    store.save_project(
        {
            "id": "demo",
            "name": "Demo",
            "home_route": "/",
            "pages": [
                {"id": "home", "name": "Home", "route": "/", "page": "home.html", "is_home": True},
                {"id": "legal", "name": "Legal", "route": "/legal", "page": "legal.html"},
            ],
        }
    )

    updated = store.delete_page("demo", "home")

    assert updated is not None
    assert updated["home_route"] == "/legal"
    assert len(updated["pages"]) == 1
    assert updated["pages"][0]["id"] == "legal"


def test_product_ops_register_authenticate_and_change_password():
    from src.productization.service import ProductOpsService

    class FakeResult:
        def __init__(self, scalar_value=None, row=None, rows=None):
            self._scalar_value = scalar_value
            self._row = row
            self._rows = rows or []

        def scalar(self):
            return self._scalar_value

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    class FakeRow:
        def __init__(self, mapping):
            self._mapping = mapping

    class FakeSession:
        def __init__(self):
            self.users = {}
            self.roles = {}

        async def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "SELECT COUNT(*) FROM app_users" in sql:
                return FakeResult(scalar_value=len(self.users))
            if "SELECT * FROM app_users WHERE email = :email" in sql:
                email = params["email"]
                for user in self.users.values():
                    if user.get("email") == email:
                        return FakeResult(row=FakeRow(user))
                return FakeResult(row=None)
            if "SELECT * FROM app_users WHERE id = :id" in sql:
                user = self.users.get(params["id"])
                return FakeResult(row=FakeRow(user) if user else None)
            if "INSERT INTO app_users" in sql:
                self.users[params["id"]] = {
                    "id": params["id"],
                    "name": params["name"],
                    "email": params["email"],
                    "status": params["status"],
                    "password_hash": params["password_hash"],
                    "password_salt": params["password_salt"],
                    "email_verified": params["email_verified"],
                    "last_login_at": params["last_login_at"],
                    "metadata": json.loads(params["metadata"]),
                    "created_at": None,
                    "updated_at": None,
                }
                return FakeResult()
            if "DELETE FROM app_user_roles WHERE user_id = :id" in sql:
                self.roles[params["id"]] = []
                return FakeResult()
            if "INSERT INTO app_user_roles" in sql:
                self.roles.setdefault(params["user_id"], []).append(params["role_name"])
                return FakeResult()
            if "SELECT role_name FROM app_user_roles WHERE user_id = :id" in sql:
                rows = [FakeRow({"role_name": role}) for role in self.roles.get(params["id"], [])]
                return FakeResult(rows=rows)
            raise AssertionError(f"Unexpected SQL: {sql}")

        async def commit(self):
            return None

    async def run():
        session = FakeSession()
        service = ProductOpsService(session)
        account = await service.register_user(email="user@example.com", password="Password123", name="User")
        assert account["email"] == "user@example.com"
        assert "admin" in account["roles"]

        logged_in = await service.authenticate_user(email="user@example.com", password="Password123")
        assert logged_in is not None
        assert logged_in["user_id"] == account["user_id"]

        changed = await service.change_user_password(
            user_id=account["user_id"],
            current_password="Password123",
            new_password="NewPassword123",
        )
        assert changed is not None

        logged_in_again = await service.authenticate_user(email="user@example.com", password="NewPassword123")
        assert logged_in_again is not None

    asyncio.run(run())


def test_auth_bootstrap_degrades_when_database_is_unavailable():
    from src.api.routes import get_auth_bootstrap

    class BrokenSession:
        async def execute(self, *args, **kwargs):
            raise ConnectionRefusedError("database unavailable")

    result = asyncio.run(get_auth_bootstrap(db=BrokenSession()))

    assert result["database_available"] is False
    assert result["registration_open"] is False
    assert result["first_user_becomes_admin"] is False
    assert result["user_count"] == 0
