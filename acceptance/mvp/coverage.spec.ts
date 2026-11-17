import { test as base, expect } from '@playwright/test';
import { createCoverageTest } from '../../integrations/playwright/index.cjs';
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const test = createCoverageTest(base);

test('MVP · девять полей, реальные запросы и точные счётчики', async ({ request, coverage }, info) => {
  const proxy = process.env.MVP_PROXY!;
  const endpoint = process.env.MVP_UPSTREAM + '/graphql';
  const headers = { 'X-Coverage-Api-Key': process.env.MVP_KEY! };
  async function create(path: string, data: object) {
    const response = await request.post(proxy + path, { headers, data });
    expect(response.status()).toBe(201);
    return response.json();
  }
  const service = await create('/api/services', { key: 'mvp-' + randomUUID(), name: 'MVP', endpointUrl: endpoint });
  const schema = await create('/api/schemas/import/introspection', { serviceId: service.id });
  const run = await create('/api/runs', { snapshotId: schema.id, name: 'MVP acceptance' });
  const execution = await coverage.trackRun(run.id);
  const runPath = proxy + '/api/runs/' + run.id;
  const query = '{ user(id:"1") { id name orders { total } } }';
  async function send(query: string) {
    return request.post(proxy + '/graphql', { headers: { ...headers, ...execution.headers }, data: { query } });
  }
  const checkpoints: { label: string, percent: number, hits: Record<string, number> }[] = [];
  async function checkpoint(label: string, covered: number, percent: number, hits: number[]) {
    const response = await request.get(runPath + '/coverage', { headers });
    expect(response.status()).toBe(200);
    const graph = await response.json();
    const coordinates = ['Query.user', 'Query.users', 'Mutation.createUser', 'User.id', 'User.name',
      'User.email', 'User.orders', 'Order.id', 'Order.total'];
    const actual = Object.fromEntries(graph.types.flatMap((t: {name: string, fields: {name: string, hits: number}[]}) =>
      t.fields.map(f => [t.name + '.' + f.name, f.hits])));
    await info.attach(label, { body: JSON.stringify({ runId: run.id, summary: graph.summary, hits: actual }, null, 2),
      contentType: 'application/json' });
    checkpoints.push({ label, percent: graph.summary.coveragePercent, hits: actual });
    expect(graph.summary).toMatchObject({ coveredFields: covered, totalFields: 9, coveragePercent: percent });
    expect(actual).toEqual(Object.fromEntries(coordinates.map((name, i) => [name, hits[i]])));
  }
  try {
    await checkpoint('До запросов', 0, 0, Array(9).fill(0));
    const user = { data: { user: { id: '1', name: 'Anna', orders: [{ total: 50 }] } } };
    expect(await (await send(query)).json()).toEqual(user);
    await checkpoint('Вложенный запрос', 5, 55.56, [1,0,0,1,1,0,1,0,1]);
    expect(await (await send('mutation { createUser(name:"Maria") { id name } }')).json())
      .toEqual({ data: { createUser: { id: '2', name: 'Maria' } } });
    await checkpoint('Создание пользователя', 6, 66.67, [1,0,1,2,2,0,1,0,1]);
    expect(await (await send(query)).json()).toEqual(user);
    const finalHits = [2,0,1,3,3,0,2,0,2];
    await checkpoint('Повтор', 6, 66.67, finalHits);
    for (const invalid of ['{', '{ unknown }']) {
      const direct = await request.post(endpoint, { data: { query: invalid } });
      const proxied = await send(invalid);
      expect(proxied.status()).toBe(direct.status());
      const body = await direct.json();
      expect(body.errors.length).toBeGreaterThan(0);
      expect(await proxied.json()).toEqual(body);
      await checkpoint('После ошибки ' + invalid, 6, 66.67, finalHits);
    }
  } finally {
    expect((await request.post(runPath + '/finish', { headers })).status()).toBe(200);
  }
  await writeFile(join(process.env.MVP_REPORT!, 'steps.json'), JSON.stringify(checkpoints, null, 2));
  const final = await request.get(runPath + '/coverage', { headers });
  expect(final.status()).toBe(200);
  await writeFile(join(process.env.MVP_REPORT!, 'coverage.json'), await final.body());
});
