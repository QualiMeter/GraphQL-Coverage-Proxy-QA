import strawberry
from fastapi import FastAPI, Request
from strawberry.fastapi import GraphQLRouter


@strawberry.type
class Order:
    id: strawberry.ID
    total: float


@strawberry.type
class User:
    id: strawberry.ID
    name: str
    email: str | None
    orders: list[Order]


users = [User(id=strawberry.ID("1"), name="Anna", email=None,
              orders=[Order(id=strawberry.ID("1"), total=50.0)])]


@strawberry.type
class Query:
    @strawberry.field
    def user(self, id: strawberry.ID) -> User | None:
        return next((user for user in users if user.id == id), None)

    @strawberry.field
    def users(self) -> list[User]:
        return users


@strawberry.type
class Mutation:
    @strawberry.mutation
    def create_user(self, name: str) -> User:
        user = User(id=strawberry.ID(str(len(users) + 1)), name=name, email=None, orders=[])
        users.append(user)
        return user


app = FastAPI()
app.include_router(GraphQLRouter(strawberry.Schema(query=Query, mutation=Mutation)), prefix="/graphql")


@app.middleware("http")
async def reject_internal_headers(request: Request, call_next):
    if any(key in request.headers for key in ("x-coverage-api-key", "x-coverage-run-id")):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Coverage credentials reached the demo API"}, status_code=400)
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok"}
