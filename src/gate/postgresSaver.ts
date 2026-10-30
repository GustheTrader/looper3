/**
 * Postgres checkpointer adapter STUB (NFR-4 target, NOT implemented).
 *
 * There is deliberately no `pg` import and no connection string anywhere in this repo: phase 2 is
 * offline paper mode. The class exists so that wiring it later is a one-line change and so that
 * using it today fails loudly instead of silently falling back to an in-memory saver.
 *
 * NFR-4 requires a direct or session-pooler Supabase URL and explicitly NOT port 6543 - that
 * belongs to the future adapter, not to this stub.
 */
import { BaseCheckpointSaver } from "@langchain/langgraph";

export const POSTGRES_SAVER_STUB_STATUS =
  "STUB: not implemented - never opens a connection, never reads a URL, not usable as a checkpointer";

export class PostgresSaverStubError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PostgresSaverStubError";
  }
}

export class PostgresCheckpointSaverStub extends BaseCheckpointSaver {
  constructor() {
    super();
    throw new PostgresSaverStubError(
      `${POSTGRES_SAVER_STUB_STATUS}. Use MemorySaver (tests) - do not point this at production.`,
    );
  }

  override async getTuple(..._args: Parameters<BaseCheckpointSaver["getTuple"]>): Promise<never> {
    throw new PostgresSaverStubError(POSTGRES_SAVER_STUB_STATUS);
  }

  override async *list(..._args: Parameters<BaseCheckpointSaver["list"]>): AsyncGenerator<never, void, unknown> {
    throw new PostgresSaverStubError(POSTGRES_SAVER_STUB_STATUS);
  }

  override async put(..._args: Parameters<BaseCheckpointSaver["put"]>): Promise<never> {
    throw new PostgresSaverStubError(POSTGRES_SAVER_STUB_STATUS);
  }

  override async putWrites(..._args: Parameters<BaseCheckpointSaver["putWrites"]>): Promise<void> {
    throw new PostgresSaverStubError(POSTGRES_SAVER_STUB_STATUS);
  }

  override async deleteThread(..._args: Parameters<BaseCheckpointSaver["deleteThread"]>): Promise<void> {
    throw new PostgresSaverStubError(POSTGRES_SAVER_STUB_STATUS);
  }
}
