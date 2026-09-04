import { describe, expect, it } from "vitest";

import { decodeScenePatchEvent, LiveSceneProtocolError } from "./patch";
import {
  applySemanticScenePatch,
  createSemanticSceneState,
  decodeSemanticScenePatchEvent,
  type SemanticScenePatchEvent,
} from "./semantic";
import { createSceneState } from "./state";

const DIGESTS = {
  beat: "2985081a36afc4116b715142c8384af329e752ce57ba41bed8e42b3b03955458",
  base: "a8a338405d84e56a062bb45ec4800cfb09e8af57bbcec8560069464ff3f7dee0",
  result: "2f47395b2aa4c0e994301c2698a2ebb5814b176d23f30035348728bfb1b54ae8",
  patch: "4cc1d990a304e1dc5c9a291c774cca4b4c0775c5413daae1ab9ac76a13af01f2",
  receipt: "518b80fd923323c07939edb97a9dad39aebd3ec475518be0074c85b890fbee47",
  certificate: "28ab5dd89fb5a900354b63a1279c2c5566eebc99f8f9142500a082d1436f485d",
} as const;

function rawSemanticEvent(): Record<string, unknown> {
  return {
    type: "semantic_scene_patch",
    generation: 17,
    attempt: 1,
    sequence: 1,
    baseRevision: 0,
    resultRevision: 1,
    patch: {
      v: 1,
      patchId: "areas__atom_triangle",
      narration: "Relate the three square areas.",
      operations: [
        {
          op: "put",
          node: {
            id: "areas__triangle",
            kind: "path",
            presentation: { enter: "draw", exit: "fade" },
            points: [
              [320, 260],
              [440, 260],
              [320, 420],
            ],
            closed: true,
            style: {
              stroke: "hsl(var(--chalk))",
              strokeWidth: 4,
              opacity: 1,
              roughness: 0.45,
              fill: "transparent",
            },
          },
        },
      ],
    },
    semantic: {
      beat: {
        v: 1,
        beatId: "beat-identity",
        narration: "Relate the three square areas.",
        act: "derive",
        directive: {
          kind: "pythagorean_area_identity",
          id: "areas",
          revealThrough: "identity",
        },
      },
      atomId: "areas__atom_triangle",
      componentId: "areas",
      role: "triangle",
      atomOrdinal: 1,
      semanticBaseRevision: 0,
      semanticResultRevision: 1,
      receipt: {
        issuer: "semantic_verifier",
        componentId: "areas",
        role: "triangle",
        nodeId: "areas__triangle",
        obligationCodes: [
          "stable_id",
          "unique_ids",
          "board_bounds",
          "right_angle",
          "hypotenuse_ratio",
        ],
        verified: true,
      },
      certificate: {
        body: {
          v: 1,
          issuer: "semantic_compiler",
          compilerVersion: "murmur.pythagorean_area_identity.v1",
          canonicalization: "murmur-json-v1",
          hashAlgorithm: "sha256",
          atomId: "areas__atom_triangle",
          beatId: "beat-identity",
          beatSha256: DIGESTS.beat,
          componentId: "areas",
          role: "triangle",
          nodeId: "areas__triangle",
          atomOrdinal: 1,
          baseSemanticRevision: 0,
          resultSemanticRevision: 1,
          baseSceneSha256: DIGESTS.base,
          resultSceneSha256: DIGESTS.result,
          patchSha256: DIGESTS.patch,
          receiptSha256: DIGESTS.receipt,
          previousCertificateSha256: null,
        },
        certificateSha256: DIGESTS.certificate,
      },
    },
  };
}

function secondSemanticEvent(
  previousCertificateSha256: string | null = DIGESTS.certificate
): Record<string, unknown> {
  const event = rawSemanticEvent();
  event.sequence = 2;
  event.baseRevision = 1;
  event.resultRevision = 2;

  const patch = event.patch as Record<string, unknown>;
  patch.patchId = "areas__atom_square_a";
  patch.operations = [
    {
      op: "put",
      node: {
        id: "areas__square_a",
        kind: "rect",
        presentation: { enter: "scale", exit: "fade" },
        x: 320,
        y: 140,
        width: 120,
        height: 120,
        style: {
          stroke: "hsl(var(--lavender))",
          strokeWidth: 3,
          opacity: 1,
          roughness: 0.45,
          fill: "transparent",
        },
      },
    },
  ];

  const semantic = event.semantic as Record<string, unknown>;
  semantic.atomId = "areas__atom_square_a";
  semantic.role = "square_a";
  semantic.atomOrdinal = 2;
  semantic.semanticBaseRevision = 1;
  semantic.semanticResultRevision = 2;
  const receipt = semantic.receipt as Record<string, unknown>;
  receipt.role = "square_a";
  receipt.nodeId = "areas__square_a";
  receipt.obligationCodes = ["stable_id", "unique_ids", "board_bounds", "attached_square"];
  const certificate = semantic.certificate as Record<string, unknown>;
  certificate.certificateSha256 = "f".repeat(64);
  const body = certificate.body as Record<string, unknown>;
  body.atomId = "areas__atom_square_a";
  body.role = "square_a";
  body.nodeId = "areas__square_a";
  body.atomOrdinal = 2;
  body.baseSemanticRevision = 1;
  body.resultSemanticRevision = 2;
  body.previousCertificateSha256 = previousCertificateSha256;
  return event;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function protocolCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("semantic live scene contract", () => {
  it("decodes an exact server event and deep-freezes every accepted layer", () => {
    const event = decodeSemanticScenePatchEvent(rawSemanticEvent());

    expect(event).toMatchObject({
      type: "semantic_scene_patch",
      generation: 17,
      baseRevision: 0,
      resultRevision: 1,
      semantic: {
        atomId: "areas__atom_triangle",
        role: "triangle",
        atomOrdinal: 1,
      },
    });
    expect(Object.isFrozen(event)).toBe(true);
    expect(Object.isFrozen(event.patch)).toBe(true);
    expect(Object.isFrozen(event.patch.operations)).toBe(true);
    expect(Object.isFrozen(event.semantic)).toBe(true);
    expect(Object.isFrozen(event.semantic.beat)).toBe(true);
    expect(Object.isFrozen(event.semantic.beat.directive)).toBe(true);
    expect(Object.isFrozen(event.semantic.receipt)).toBe(true);
    expect(Object.isFrozen(event.semantic.receipt.obligationCodes)).toBe(true);
    expect(Object.isFrozen(event.semantic.certificate)).toBe(true);
    expect(Object.isFrozen(event.semantic.certificate.body)).toBe(true);
  });

  it.each([
    ["event", (event: Record<string, unknown>) => (event.debug = true)],
    [
      "metadata",
      (event: Record<string, unknown>) =>
        ((event.semantic as Record<string, unknown>).providerTrace = "unsafe"),
    ],
    [
      "beat",
      (event: Record<string, unknown>) =>
        (((event.semantic as Record<string, unknown>).beat as Record<string, unknown>).geometry = {}),
    ],
    [
      "directive",
      (event: Record<string, unknown>) =>
        ((((event.semantic as Record<string, unknown>).beat as Record<string, unknown>)
          .directive as Record<string, unknown>).x = 10),
    ],
    [
      "receipt",
      (event: Record<string, unknown>) =>
        (((event.semantic as Record<string, unknown>).receipt as Record<string, unknown>).proof = true),
    ],
    [
      "certificate",
      (event: Record<string, unknown>) =>
        (((event.semantic as Record<string, unknown>).certificate as Record<string, unknown>).signed = false),
    ],
    [
      "certificate body",
      (event: Record<string, unknown>) => {
        const certificate = (event.semantic as Record<string, unknown>)
          .certificate as Record<string, unknown>;
        (certificate.body as Record<string, unknown>).clock = 1;
      },
    ],
  ])("rejects unknown %s fields", (_label, mutate) => {
    const event = rawSemanticEvent();
    mutate(event);
    expect(protocolCode(() => decodeSemanticScenePatchEvent(event))).toBe("invalid_event");
  });

  it("rejects invalid beat ownership, directive stages, and narration", () => {
    const wrongComponent = rawSemanticEvent();
    (((wrongComponent.semantic as Record<string, unknown>).beat as Record<string, unknown>)
      .directive as Record<string, unknown>).id = "other";
    expect(() => decodeSemanticScenePatchEvent(wrongComponent)).toThrow(/directive id/);

    const wrongStage = rawSemanticEvent();
    (((wrongStage.semantic as Record<string, unknown>).beat as Record<string, unknown>)
      .directive as Record<string, unknown>).revealThrough = "proof";
    expect(() => decodeSemanticScenePatchEvent(wrongStage)).toThrow(/revealThrough/);

    const wrongNarration = rawSemanticEvent();
    (((wrongNarration.semantic as Record<string, unknown>).beat as Record<string, unknown>)
      .narration as string) = "Say something else.";
    expect(() => decodeSemanticScenePatchEvent(wrongNarration)).toThrow(/narration/);
  });

  it("rejects role, ordinal, atom, receipt, and one-put target mismatches", () => {
    const wrongOrdinal = rawSemanticEvent();
    (wrongOrdinal.semantic as Record<string, unknown>).atomOrdinal = 2;
    expect(() => decodeSemanticScenePatchEvent(wrongOrdinal)).toThrow(/role and ordinal/);

    const wrongAtom = rawSemanticEvent();
    (wrongAtom.semantic as Record<string, unknown>).atomId = "other_atom";
    expect(() => decodeSemanticScenePatchEvent(wrongAtom)).toThrow(/deterministic semantic atom id/);

    const wrongReceipt = rawSemanticEvent();
    ((wrongReceipt.semantic as Record<string, unknown>).receipt as Record<string, unknown>).nodeId =
      "other_node";
    expect(() => decodeSemanticScenePatchEvent(wrongReceipt)).toThrow(/receipt/);

    const duplicateObligation = rawSemanticEvent();
    ((duplicateObligation.semantic as Record<string, unknown>).receipt as Record<string, unknown>)
      .obligationCodes = ["stable_id", "stable_id"];
    expect(() => decodeSemanticScenePatchEvent(duplicateObligation)).toThrow(/must be unique/);

    const remove = rawSemanticEvent();
    (remove.patch as Record<string, unknown>).operations = [
      { op: "remove", id: "areas__triangle" },
    ];
    expect(() => decodeSemanticScenePatchEvent(remove)).toThrow(/exactly one put/);
  });

  it("rejects coherent cross-field renaming away from deterministic semantic IDs", () => {
    const renamedAtom = rawSemanticEvent();
    (renamedAtom.patch as Record<string, unknown>).patchId = "renamed_atom";
    const renamedAtomSemantic = renamedAtom.semantic as Record<string, unknown>;
    renamedAtomSemantic.atomId = "renamed_atom";
    const renamedAtomCertificate = renamedAtomSemantic.certificate as Record<string, unknown>;
    (renamedAtomCertificate.body as Record<string, unknown>).atomId = "renamed_atom";
    expect(() => decodeSemanticScenePatchEvent(renamedAtom)).toThrow(
      /deterministic semantic atom id areas__atom_triangle/
    );

    const renamedNode = rawSemanticEvent();
    const renamedNodePatch = renamedNode.patch as Record<string, unknown>;
    const renamedNodeOperations = renamedNodePatch.operations as Record<string, unknown>[];
    (renamedNodeOperations[0].node as Record<string, unknown>).id = "renamed_node";
    const renamedNodeSemantic = renamedNode.semantic as Record<string, unknown>;
    (renamedNodeSemantic.receipt as Record<string, unknown>).nodeId = "renamed_node";
    const renamedNodeCertificate = renamedNodeSemantic.certificate as Record<string, unknown>;
    (renamedNodeCertificate.body as Record<string, unknown>).nodeId = "renamed_node";
    expect(() => decodeSemanticScenePatchEvent(renamedNode)).toThrow(
      /deterministic semantic node id areas__triangle/
    );
  });

  it("treats well-formed server digest fields as opaque structural claims", () => {
    const event = rawSemanticEvent();
    const certificate = (event.semantic as Record<string, unknown>)
      .certificate as Record<string, unknown>;
    const body = certificate.body as Record<string, unknown>;
    for (const key of [
      "beatSha256",
      "baseSceneSha256",
      "resultSceneSha256",
      "patchSha256",
      "receiptSha256",
    ]) {
      body[key] = "0".repeat(64);
    }
    certificate.certificateSha256 = "0".repeat(64);

    expect(decodeSemanticScenePatchEvent(event).semantic.certificate).toMatchObject({
      certificateSha256: "0".repeat(64),
      body: { patchSha256: "0".repeat(64) },
    });
  });

  it("validates the certificate structure, absolute ordinal, and atom bindings", () => {
    const badDigest = rawSemanticEvent();
    (((badDigest.semantic as Record<string, unknown>).certificate as Record<string, unknown>)
      .body as Record<string, unknown>).patchSha256 = "ABC";
    expect(() => decodeSemanticScenePatchEvent(badDigest)).toThrow(/lowercase SHA-256/);

    const wrongCompiler = rawSemanticEvent();
    (((wrongCompiler.semantic as Record<string, unknown>).certificate as Record<string, unknown>)
      .body as Record<string, unknown>).compilerVersion = "murmur.other.v1";
    expect(() => decodeSemanticScenePatchEvent(wrongCompiler)).toThrow(/compilerVersion/);

    const wrongIdentity = rawSemanticEvent();
    (((wrongIdentity.semantic as Record<string, unknown>).certificate as Record<string, unknown>)
      .body as Record<string, unknown>).nodeId = "other_node";
    expect(() => decodeSemanticScenePatchEvent(wrongIdentity)).toThrow(/certificate identity/);

    const wrongRevision = rawSemanticEvent();
    (((wrongRevision.semantic as Record<string, unknown>).certificate as Record<string, unknown>)
      .body as Record<string, unknown>).resultSemanticRevision = 2;
    expect(protocolCode(() => decodeSemanticScenePatchEvent(wrongRevision))).toBe(
      "revision_mismatch"
    );
  });

  it("creates only immutable unique semantic role prefixes", () => {
    const input = {
      revision: 2,
      components: [
        {
          kind: "pythagorean_area_identity" as const,
          id: "areas",
          revealedRoles: ["triangle", "square_a"] as const,
        },
      ],
      certificateHeadSha256: "f".repeat(64),
    };
    const state = createSemanticSceneState(input);

    expect(state).not.toBe(input);
    expect(Object.isFrozen(state)).toBe(true);
    expect(Object.isFrozen(state.components)).toBe(true);
    expect(Object.isFrozen(state.components[0])).toBe(true);
    expect(Object.isFrozen(state.components[0].revealedRoles)).toBe(true);

    const skippedRole = clone(input) as unknown as Record<string, unknown>;
    const skippedComponents = skippedRole.components as Record<string, unknown>[];
    skippedComponents[0].revealedRoles = ["triangle", "label_a2"];
    expect(() =>
      createSemanticSceneState(
        skippedRole as unknown as Parameters<typeof createSemanticSceneState>[0]
      )
    ).toThrow(/ordered role prefix/);

    const duplicate = clone(input) as unknown as Record<string, unknown>;
    const duplicateComponents = duplicate.components as Record<string, unknown>[];
    duplicate.components = [duplicateComponents[0], duplicateComponents[0]];
    expect(() =>
      createSemanticSceneState(
        duplicate as unknown as Parameters<typeof createSemanticSceneState>[0]
      )
    ).toThrow(/duplicated/);
  });

  it("applies semantic atoms in lockstep and advances the certificate head", () => {
    const lowLevelBase = createSceneState({ revision: 0, nodes: [] });
    const semanticBase = createSemanticSceneState({ revision: 0, components: [] });
    const first = applySemanticScenePatch(
      lowLevelBase,
      semanticBase,
      rawSemanticEvent() as unknown as SemanticScenePatchEvent
    );

    expect(first.scene.revision).toBe(1);
    expect(first.scene.nodes.map((node) => node.id)).toEqual(["areas__triangle"]);
    expect(first.plan.steps).toHaveLength(1);
    expect(first.plan.steps[0]).toMatchObject({
      type: "enter",
      id: "areas__triangle",
      node: { id: "areas__triangle" },
    });
    expect(first.semanticScene).toEqual({
      revision: 1,
      components: [
        {
          kind: "pythagorean_area_identity",
          id: "areas",
          revealedRoles: ["triangle"],
        },
      ],
      certificateHeadSha256: DIGESTS.certificate,
    });
    expect(lowLevelBase).toEqual({ revision: 0, nodes: [] });
    expect(semanticBase).toEqual({ revision: 0, components: [] });

    const second = applySemanticScenePatch(
      first.scene,
      first.semanticScene,
      secondSemanticEvent() as unknown as SemanticScenePatchEvent
    );
    expect(second.scene.revision).toBe(2);
    expect(second.scene.nodes.map((node) => node.id)).toEqual([
      "areas__triangle",
      "areas__square_a",
    ]);
    expect(second.semanticScene.components[0].revealedRoles).toEqual([
      "triangle",
      "square_a",
    ]);
    expect(second.semanticScene.certificateHeadSha256).toBe("f".repeat(64));
  });

  it("rejects low-level scene drift from the registered semantic prefix", () => {
    const first = applySemanticScenePatch(
      createSceneState({ revision: 0, nodes: [] }),
      createSemanticSceneState({ revision: 0, components: [] }),
      rawSemanticEvent() as unknown as SemanticScenePatchEvent
    );
    const second = applySemanticScenePatch(
      first.scene,
      first.semanticScene,
      secondSemanticEvent() as unknown as SemanticScenePatchEvent
    );

    expect(() =>
      applySemanticScenePatch(
        createSceneState({ revision: 1, nodes: [] }),
        first.semanticScene,
        secondSemanticEvent() as unknown as SemanticScenePatchEvent
      )
    ).toThrow(/revealed role triangle is missing stable node areas__triangle/);

    const prematureIdentity = {
      ...second.scene.nodes[1],
      id: "areas__identity",
    };
    expect(() =>
      applySemanticScenePatch(
        createSceneState({
          revision: 1,
          nodes: [first.scene.nodes[0], prematureIdentity],
        }),
        first.semanticScene,
        secondSemanticEvent() as unknown as SemanticScenePatchEvent
      )
    ).toThrow(/unrevealed role identity already has stable node areas__identity/);
  });

  it("rejects an existing target instead of accepting a semantic update motion", () => {
    const first = applySemanticScenePatch(
      createSceneState({ revision: 0, nodes: [] }),
      createSemanticSceneState({ revision: 0, components: [] }),
      rawSemanticEvent() as unknown as SemanticScenePatchEvent
    );
    const second = applySemanticScenePatch(
      first.scene,
      first.semanticScene,
      secondSemanticEvent() as unknown as SemanticScenePatchEvent
    );

    expect(() =>
      applySemanticScenePatch(
        createSceneState({ revision: 1, nodes: second.scene.nodes }),
        first.semanticScene,
        secondSemanticEvent() as unknown as SemanticScenePatchEvent
      )
    ).toThrow(/incoming semantic target areas__square_a must be absent/);
  });

  it("rejects a gap or fork in the accepted semantic chain without mutating either base", () => {
    const first = applySemanticScenePatch(
      createSceneState({ revision: 0, nodes: [] }),
      createSemanticSceneState({ revision: 0, components: [] }),
      rawSemanticEvent() as unknown as SemanticScenePatchEvent
    );
    const sceneBefore = clone(first.scene);
    const semanticBefore = clone(first.semanticScene);

    expect(() =>
      applySemanticScenePatch(
        first.scene,
        first.semanticScene,
        secondSemanticEvent("0".repeat(64)) as unknown as SemanticScenePatchEvent
      )
    ).toThrow(/chain head/);
    expect(first.scene).toEqual(sceneBefore);
    expect(first.semanticScene).toEqual(semanticBefore);

    const skipped = secondSemanticEvent();
    const skippedPatch = skipped.patch as Record<string, unknown>;
    skippedPatch.patchId = "areas__atom_label_a2";
    const skippedOperations = skippedPatch.operations as Record<string, unknown>[];
    (skippedOperations[0].node as Record<string, unknown>).id = "areas__label_a2";
    (skipped.semantic as Record<string, unknown>).atomOrdinal = 3;
    (skipped.semantic as Record<string, unknown>).atomId = "areas__atom_label_a2";
    const certificate = (skipped.semantic as Record<string, unknown>).certificate as Record<
      string,
      unknown
    >;
    const body = certificate.body as Record<string, unknown>;
    body.atomId = "areas__atom_label_a2";
    body.atomOrdinal = 3;
    body.role = "label_a2";
    body.nodeId = "areas__label_a2";
    (skipped.semantic as Record<string, unknown>).role = "label_a2";
    const receipt = (skipped.semantic as Record<string, unknown>).receipt as Record<
      string,
      unknown
    >;
    receipt.role = "label_a2";
    receipt.nodeId = "areas__label_a2";
    expect(() =>
      applySemanticScenePatch(
        first.scene,
        first.semanticScene,
        skipped as unknown as SemanticScenePatchEvent
      )
    ).toThrow(/exact next role/);
  });

  it("rejects committed roles without a head and an orphan head without roles", () => {
    const first = applySemanticScenePatch(
      createSceneState({ revision: 0, nodes: [] }),
      createSemanticSceneState({ revision: 0, components: [] }),
      rawSemanticEvent() as unknown as SemanticScenePatchEvent
    );
    const missingHead = createSemanticSceneState({
      revision: 1,
      components: first.semanticScene.components,
    });
    expect(() =>
      applySemanticScenePatch(
        first.scene,
        missingHead,
        secondSemanticEvent(null) as unknown as SemanticScenePatchEvent
      )
    ).toThrow(/committed roles and certificate chain head must agree/);

    const orphanHead = createSemanticSceneState({
      revision: 1,
      components: [
        {
          kind: "pythagorean_area_identity",
          id: "areas",
          revealedRoles: [],
        },
      ],
      certificateHeadSha256: DIGESTS.certificate,
    });
    const firstAtRevisionOne = rawSemanticEvent();
    firstAtRevisionOne.baseRevision = 1;
    firstAtRevisionOne.resultRevision = 2;
    const semantic = firstAtRevisionOne.semantic as Record<string, unknown>;
    semantic.semanticBaseRevision = 1;
    semantic.semanticResultRevision = 2;
    const certificate = semantic.certificate as Record<string, unknown>;
    const body = certificate.body as Record<string, unknown>;
    body.baseSemanticRevision = 1;
    body.resultSemanticRevision = 2;
    body.previousCertificateSha256 = DIGESTS.certificate;
    expect(() =>
      applySemanticScenePatch(
        createSceneState({ revision: 1, nodes: [] }),
        orphanHead,
        firstAtRevisionOne as unknown as SemanticScenePatchEvent
      )
    ).toThrow(/committed roles and certificate chain head must agree/);
  });

  it("does not weaken the raw scene-patch discriminator or nested patch grammar", () => {
    const semantic = rawSemanticEvent();
    expect(() => decodeScenePatchEvent(semantic)).toThrow(/unknown field semantic/);

    const invalidNode = rawSemanticEvent();
    const operations = (invalidNode.patch as Record<string, unknown>).operations as Record<
      string,
      unknown
    >[];
    (operations[0].node as Record<string, unknown>).script = "alert(1)";
    expect(protocolCode(() => decodeSemanticScenePatchEvent(invalidNode))).toBe("invalid_node");
  });
});
