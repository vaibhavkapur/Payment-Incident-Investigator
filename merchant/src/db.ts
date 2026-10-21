import pg from "pg";
import { config } from "./config.js";

export const pool = new pg.Pool({ connectionString: config.DATABASE_URL, max: 10 });

export type Queryable = pg.Pool | pg.PoolClient;

export async function withTx<T>(fn: (client: pg.PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("begin");
    const result = await fn(client);
    await client.query("commit");
    return result;
  } catch (err) {
    await client.query("rollback").catch(() => undefined);
    throw err;
  } finally {
    client.release();
  }
}

export async function one<T>(q: Queryable, text: string, params: unknown[] = []): Promise<T | null> {
  const res = await q.query(text, params);
  return (res.rows[0] as T) ?? null;
}

export async function many<T>(q: Queryable, text: string, params: unknown[] = []): Promise<T[]> {
  const res = await q.query(text, params);
  return res.rows as T[];
}
