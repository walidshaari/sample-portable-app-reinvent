# Follow one request

Automated, captioned evidence replay. No voiceover.

## 00s · Where does the request go?

Status: CODE TOUR. Source: `base_python/clean`.

HTTP / use case / port / adapter

The business action stays inside the core.

## 04s · The action asks for behavior

Status: CURRENT SOURCE. Source: `base_python/clean/application/use_cases/create_user.py:18-22`.

        user_id = str(uuid.uuid4())
        user = User(user_id, name, email)

        await self._user_repository.create(user)
        return user

No storage SDK or web framework enters this action.

## 09s · The core runs offline

Status: FRESH LOCAL PROOF. Source: `demo/02-testability.sh`.

$ bash demo/02-testability.sh
(no matches)
.............................                                            [100%]

Fresh local command: no SDK/framework imports in the core; core tests pass.

## 14s · A smaller debugging surface

Status: PRESENTER LINE. Source: `talk/arc401-stage-walkthrough.md`.

Entity rule  •  use case  •  adapter  •  HTTP response

Each failure has a place to investigate and a test to run.
