/**
 * 内核测试：末尾表达式结果捕获、日志捕获、emitImage artifact、fresh-context
 * 语义、超时、模块解析根。
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { JsKernelRunner, prepareUserCode } from "../dist/lib.js";

function makeRunner(overrides = {}) {
  const dir = mkdtempSync(join(tmpdir(), "illusion-kernel-test-"));
  const runner = new JsKernelRunner({
    pluginRoot: "/tmp/plugin-root",
    configDir: join(dir, "config"),
    moduleDirs: [],
    broker: null,
    cwd: dir,
    timeoutMs: 5_000,
    ...overrides,
  });
  return { runner, dir };
}

test("prepareUserCode：末尾表达式 → return；多语句保留", () => {
  assert.equal(
    prepareUserCode("const a = 1;\n({ a });"),
    "const a = 1;\nreturn ({ a });",
  );
  // 声明结尾不返回值
  assert.equal(prepareUserCode("const a = 1;"), "const a = 1;");
  // 解析失败原样返回（运行时报错）
  assert.equal(prepareUserCode("const ("), "const (");
});

test("kernel run：结果捕获 + 日志", async () => {
  const { runner, dir } = makeRunner();
  try {
    const output = await runner.run("console.log('hello'); await Promise.resolve(1); 40 + 2");
    assert.equal(output.result, 42);
    assert.match(output.logs, /hello/);
    assert.equal(output.error, undefined);
    assert.ok(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("kernel run：fresh context（变量不跨调用持久化）", async () => {
  const { runner, dir } = makeRunner();
  try {
    await runner.run("globalThis.x = 1; var y = 2;");
    const output = await runner.run("typeof x === 'undefined' && typeof y === 'undefined'");
    assert.equal(output.result, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("kernel run：异常捕获（name/message/stack）", async () => {
  const { runner, dir } = makeRunner();
  try {
    const output = await runner.run("throw new TypeError('boom')");
    assert.equal(output.error.name, "TypeError");
    assert.equal(output.error.message, "boom");
    assert.ok(output.error.stack.includes("TypeError"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("kernel run：emitImage 落盘并回传 base64", async () => {
  const { runner, dir } = makeRunner();
  try {
    // 1x1 PNG
    const pngBase64 =
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
    const output = await runner.run(
      `await nodeRepl.emitImage("${pngBase64}"); "done"`,
    );
    assert.equal(output.result, "done");
    assert.equal(output.images.length, 1);
    assert.equal(output.images[0].mimeType, "image/png");
    assert.equal(output.browserScreenshotPaths.length, 1);
    const savedPath = output.browserScreenshotPaths[0];
    assert.ok(existsSync(savedPath), "artifact written");
    assert.equal(readFileSync(savedPath).toString("base64"), pngBase64);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("kernel run：nodeRepl.write 与 importModule", async () => {
  const { runner, dir } = makeRunner();
  try {
    const output = await runner.run(
      `nodeRepl.write('w1'); const { join } = await importModule('node:path'); typeof join`,
    );
    assert.equal(output.result, "function");
    assert.match(output.logs, /w1/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("kernel run：受限 process（env 白名单）", async () => {
  const { runner, dir } = makeRunner();
  try {
    const output = await runner.run(
      "process.env.ILLUSION_PLUGIN_ROOT === undefined || typeof process.env.ILLUSION_PLUGIN_ROOT === 'string'",
    );
    assert.equal(output.result, true);
    const platform = await runner.run("typeof process.cwd() === 'string'");
    assert.equal(platform.result, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("kernel run：超时保护（死循环被 vm timeout 终止）", async () => {
  const { runner, dir } = makeRunner({ timeoutMs: 300 });
  try {
    const output = await runner.run("while (true) {}");
    assert.ok(output.error, "should report error");
    assert.match(output.error.message, /timed out|interrupted/i);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
