import { describe, expect, it } from "vitest";
import { b64url, buildAppJwt, nextLink } from "../src/github";

function toPem(der: ArrayBuffer): string {
  const b64 = btoa(String.fromCharCode(...new Uint8Array(der)));
  return `-----BEGIN PRIVATE KEY-----\n${b64.match(/.{1,64}/g)!.join("\n")}\n-----END PRIVATE KEY-----\n`;
}
const dec = (s: string) => JSON.parse(atob(s.replace(/-/g, "+").replace(/_/g, "/")));

describe("app jwt", () => {
  it("builds and verifies an RS256 jwt", async () => {
    const kp = await crypto.subtle.generateKey(
      { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
    const pem = toPem((await crypto.subtle.exportKey("pkcs8", kp.privateKey)) as ArrayBuffer);
    const now = 1_800_000_000;
    const jwt = await buildAppJwt("12345", pem, now);
    const [h, p, s] = jwt.split(".");
    expect(dec(h)).toEqual({ alg: "RS256", typ: "JWT" });
    expect(dec(p)).toEqual({ iat: now - 60, exp: now + 480, iss: "12345" });
    const std = s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4);
    const sig = Uint8Array.from(atob(std), (c) => c.charCodeAt(0));
    const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", kp.publicKey, sig, new TextEncoder().encode(`${h}.${p}`));
    expect(ok).toBe(true);
  });
  it("helpers", () => {
    expect(b64url("??>>")).toBe("Pz8-Pg");
    expect(nextLink('<https://a/x?page=2>; rel="next", <https://a/x?page=5>; rel="last"')).toBe("https://a/x?page=2");
    expect(nextLink(null)).toBeNull();
  });
});
