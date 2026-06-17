import assert from 'node:assert/strict';
import test from 'node:test';

import { readErrorResponseMessage } from '../src/api/http.js';

test('readErrorResponseMessage reads malformed error bodies only once', async () => {
  let readCount = 0;
  const response = {
    status: 500,
    async text() {
      readCount += 1;
      if (readCount > 1) {
        throw new Error("Failed to execute 'text' on 'Response': body stream already read");
      }
      return '{broken-json';
    }
  };

  const message = await readErrorResponseMessage(response);

  assert.equal(message, '{broken-json');
  assert.equal(readCount, 1);
});

test('readErrorResponseMessage still prefers JSON detail when available', async () => {
  const message = await readErrorResponseMessage({
    status: 422,
    async text() {
      return JSON.stringify({ detail: 'datasource_id is required' });
    }
  });

  assert.equal(message, 'datasource_id is required');
});
