# Публичная витрина FORMA и ручное покрытие на VPS

Цель: без нового DNS открыть магазин по **https://graphql.rebyte.fun/shop/**
на существующем HTTPS-хосте, сохранив API магазина и PostgreSQL только внутри VPS.
Все ручные GraphQL-запросы витрины должны проходить через Coverage Proxy
и записываться в **отдельный активный прогон FORMA Demo Shop**.

> Этот документ — план применения на VPS, а не подтверждение публикации.
> Не объявляйте ссылку рабочей до проверки HTTPS, входа, покупки и роста hits.

## 1. Проверить предварительные условия

- VPS обслуживает `graphql.rebyte.fun` через Nginx с действующим TLS.
- QA checkout в `/opt/graphql-coverage-qa` обновлён до версии с относительными
  путями `assets/...`, `api/graphql` и переменными `SHOP_COVERAGE_*`.
- Демо-магазин уже запускается через `examples/shop/compose.yaml` на
  loopback-портах `127.0.0.1:9110` (витрина) и `127.0.0.1:9100` (API).
- В Coverage сервис **FORMA Demo Shop** указывает на доступный **из Coverage**
  upstream GraphQL магазина. На текущем стенде для него настроен ограниченный
  Nginx-маршрут `/demo-shop/graphql`; не делайте его публичным.
- HTTPS-запросы из контейнера витрины к `https://graphql.rebyte.fun/graphql`
  должны проходить успешно.
- Не запускайте ручное демо одновременно с E2E, меняющими те же товары.

## 2. Создать отдельный активный run FORMA

1. В `https://graphql.rebyte.fun/coverage/` авторизуйтесь оператором с service grant к FORMA.
2. Выберите **FORMA Demo Shop** и актуальный снимок GraphQL-схемы.
3. Создайте **новый активный прогон**, например «FORMA — ручное демо магазина».
4. Запишите полный UUID прогона. Не завершайте его до конца демонстрации.

**Почему отдельный:** QA E2E и ручные покупки можно сравнивать независимо.
Если хотите совместный результат, оба клиента могут использовать один активный run,
но тогда их обращения смешиваются. После завершения run витрина через Coverage
перестанет успешно отправлять GraphQL-запросы, пока UUID не будет заменён.

## 3. Настроить секреты на VPS, не в Git

Выполните на VPS от root:

```sh
sudo install -m 600 -o root -g root /dev/null /etc/graphql-forma-shop.env
sudo nano /etc/graphql-forma-shop.env
```

Заполните файл вручную:

```dotenv
SHOP_GRAPHQL_URL=https://graphql.rebyte.fun/graphql
SHOP_COVERAGE_API_KEY=ВСТАВЬТЕ_СУЩЕСТВУЮЩИЙ_COVERAGE_API_KEY
SHOP_COVERAGE_RUN_ID=UUID_АКТИВНОГО_RUN_FORMA
SHOP_QA_PANEL_URL=https://graphql.rebyte.fun/qa/
```

`SHOP_COVERAGE_API_KEY` — **администраторский ключ Coverage**: его передаёт
только Python-сервер витрины, браузер его не видит. Не добавляйте ключ в
Compose YAML, GitHub, README, параметры CLI и скриншоты; не выводите
`docker compose config` с этим файлом в публичные логи.
У витрины публичный GraphQL-ретранслятор, поэтому контролируйте использование
этого ключа и ограничивайте внешнюю нагрузку средствами Nginx.

## 4. Пересобрать витрину с подключением к Coverage

```sh
cd /opt/graphql-coverage-qa
docker compose --env-file /etc/graphql-forma-shop.env -p gql-commerce-demo -f examples/shop/compose.yaml up --build -d --wait
curl -fsS http://127.0.0.1:9110/api/config
curl -fsS http://127.0.0.1:9110/health
```

В `/api/config` ожидаются `coverageEnabled: true`, нужный `runId` и
`qaPanelUrl`. Ключ из ответа отсутствует. **Никаких секретов в браузер не переносить.**

## 5. Открыть только витрину через Nginx

```sh
sudo install -m 644 deploy/nginx/forma-shop-location.conf /etc/nginx/snippets/forma-shop.conf
sudo nano /etc/nginx/sites-available/graphql.rebyte.fun
```

Внутри уже существующего `server` для `graphql.rebyte.fun:443` добавьте:

```nginx
include /etc/nginx/snippets/forma-shop.conf;
```

Затем:

```sh
sudo nginx -t
sudo systemctl reload nginx
```

Порт `9100` и порт `9110` **не открывайте** в firewall. Служебный маршрут
`/demo-shop/graphql` сохраняет своё прежнее ограничение по IP.
Внешний пользователь обращается только к `/shop/`, `/shop/assets/...`
и `/shop/api/graphql`; Nginx переписывает префикс на loopback-витрину.

## 6. Проверить перед отправкой экспертам

1. Откройте `https://graphql.rebyte.fun/shop/` с внешнего устройства.
2. Убедитесь, что CSS, изображения и JS загрузились; в Network нет 404 на `/assets` и `/api/graphql` у корня сайта.
3. Откройте `https://graphql.rebyte.fun/shop/api/config`: виден правильный `runId`, `coverageEnabled: true`, но **не ключ**.
4. Войдите под тестовой Alice через кнопку демо-профиля; добавьте товар, оформите заказ, запомните ID, затем отмените заказ.
5. В Coverage откройте **тот же** активный run и нажмите «Обновить покрытие»: должны появиться или увеличиться `hits`, а во вкладках обращений и операций — GraphQL-запросы из магазина.
6. Проверьте ссылку на TestLab в подвале магазина: `https://graphql.rebyte.fun/qa/`.
7. Проверьте, что снаружи всё ещё недоступен служебный `/demo-shop/graphql` без разрешённого источника.
8. После успешной проверки замените локальное описание в `README_EXPERTS.md` на публичный адрес магазина.

**Важно:** публичный магазин позволяет посетителям создавать и отменять
фиктивные заказы, менять тестовые остатки и наращивать статистику. Перед
защитой проверьте состояние тестовой БД, оставьте запас товаров и используйте
отдельный ручной прогон. При необходимости дополнительно настройте
Nginx rate limiting для `/shop/api/graphql` до публичного запуска.

## 7. Возврат к прежнему состоянию

Удалите только добавленную строку `include` из HTTPS-vhost Nginx,
проверьте `nginx -t` и выполните reload. Витрина останется доступной только
по `127.0.0.1:9110`. Чтобы вернуть прямые локальные вызовы API, запускайте
Compose без секретного env-файла (или с `SHOP_GRAPHQL_URL=http://api:9100/graphql`
и пустыми `SHOP_COVERAGE_*`). Данные PostgreSQL сохраняются, пока не удалён volume.
