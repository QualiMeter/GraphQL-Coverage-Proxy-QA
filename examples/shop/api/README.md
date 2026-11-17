# Самостоятельный GraphQL API

Небольшой сервис заказов с настоящим исполнением GraphQL, отдельной PostgreSQL, входом по email/password и серверными сессиями. Это самостоятельный тестовый upstream; он не подменяет backend Coverage Proxy и не изменяет его код. Данные вымышленные и предназначены для проверок, поведение проверяется по фактическим HTTP-ответам и сохранённому состоянию БД.

## Отдельный запуск

Из корня QA-репозитория:

```powershell
docker compose -p gql-commerce-demo -f examples/shop/compose.yaml up --build -d
```

GraphQL: `http://localhost:9100/graphql`, схема: `/schema.graphql`, health: `/health`. Данные сохраняются в отдельном Docker volume. Остановка без удаления данных:

```powershell
docker compose -p gql-commerce-demo -f examples/shop/compose.yaml down
```

Автоматические тесты используют другой уникальный Compose project и временную БД, поэтому этот экземпляр не сбрасывается.

## Учётные записи тестового окружения

`alice@example.test`, `bob@example.test`, `admin@example.test`. Пароль всех демо-аккаунтов: `test-password-2026`. Это открытые вымышленные тестовые credentials, не production-секреты. Сервис привязан к localhost. Не предназначен для публичного production; ограничения нагрузки и защита от перебора паролей здесь не реализованы.

```graphql
mutation Login($email: String!, $password: String!) {
  login(email: $email, password: $password) {
    token
    user { id email role }
  }
}
```

Полученный token передаётся последующим запросам как `Authorization: Bearer <token>`. В БД хранится SHA-256 токена, срок сессии — один час. Пароли хранятся как PBKDF2-HMAC-SHA256 с индивидуальной солью.

## Бизнес-правила

- Каталог доступен без входа. Пагинация limit 1..100, offset >= 0; цены целым числом копеек.
- Покупатель видит свои заказы. Чужой ID даёт NOT_FOUND; список чужих заказов не раскрывается. Администратор может читать и отменять заказы, видеть статистику.
- Заказ содержит 1..20 разных товаров, quantity 1..1000. Итог вычисляет сервер. Названия и цены фиксируются в строках заказа.
- Все списания и создание заказа — одна транзакция. Если одного товара нет, изменений не происходит.
- Продукты блокируются в стабильном порядке через SELECT FOR UPDATE. Два покупателя не могут купить один остаток дважды.
- idempotencyKey обязателен (6..100 символов). Один пользователь + один ключ + одинаковые позиции возвращают тот же заказ. Другой состав с тем же ключом — IDEMPOTENCY_CONFLICT.
- Отмена возвращает товар один раз; повтор отмены не увеличивает остаток повторно.
- Заказы, профили и сессии переживают перезапуск процесса.
- HTTP 200 может содержать GraphQL errors. Некорректный HTTP-конверт возвращает 400. GraphQL validation выполняется библиотекой graphql-core, а не специальными ответами тестового сервера.

## Пример оформления

```graphql
mutation Checkout($input: CreateOrderInput!) {
  createOrder(input: $input) {
    id status totalCents
    items { name quantity unitPriceCents totalCents }
  }
}
```

```json
{
  "input": {
    "items": [
      {"productId": "keyboard", "quantity": 1},
      {"productId": "cable", "quantity": 2}
    ],
    "idempotencyKey": "example-order-001"
  }
}
```

В SQL-схеме есть ограничения положительных количеств и неотрицательных остатков, внешние ключи и уникальность idempotency key в рамках покупателя. Тесты не читают SQL, чтобы получить ожидаемый бизнес-результат: они сравнивают независимые HTTP-запросы, а перед операцией читают исходный каталог.
