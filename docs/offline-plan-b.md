# Сохранённый отчёт для офлайн-показа

Создайте пакет из конкретного завершённого прогона. Укажите папку, где лежат index.html и results.json либо coverage.json.

```powershell
python -m coverage_qa offline bundle --source reports/runs/<id> --output reports/offline-showcase
```

Для MVP передайте reports/mvp/<id>. Откройте reports/offline-showcase/showcase.html. В пакете находятся исходный отчёт, доступные выгрузки, изображения и manifest.json. Карточка показывает реальные UUID и показатели каждого сохранённого прогона. Сценарии и их статусы берутся из results.json. Если источник содержит только граф MVP, сведения о сценариях обозначены как отсутствующие.

При запуске без --source выбирается наиболее недавно записанный доступный отчёт. При отсутствии данных показывается «Нет сохранённого прогона». Чтобы зафиксировать результат для защиты, используйте явный --source. Числа покрытия считаются по полям OBJECT, как в API бэкенда.

```powershell
python -m coverage_qa offline serve --source reports/runs/<id> --output reports/offline-showcase --port 9030
```

Откройте http://127.0.0.1:9030/showcase.html. Пакет содержит результаты выбранного запуска. Для новой демонстрации с живыми запросами запустите магазин, PostgreSQL, Coverage Proxy и панель TestLab по основному README. Для диагностики доступности сервисов выполните `python -m coverage_qa offline check`.
