/**
 * Step logic for the upload wizard (upload -> review quality -> train). Pure functions so the gating can be tested.
 * This only decides what the page offers: `POST /train` is refused by the API for any role without train_models.
 */

import type { Dataset, Role } from "@/lib/types";

export type TrainGateStatus = "ready" | "locked" | "forbidden";

export interface TrainGate {
  status: TrainGateStatus;
  /** Why the step cannot be used yet, in words the user can act on. Null when ready. */
  reason: string | null;
}

export interface WizardInput {
  canTrain: boolean;
  role?: Role | null;
  dataset: Pick<Dataset, "id" | "status"> | null;
  reviewed: boolean;
}

export function trainGate({ canTrain, role, dataset, reviewed }: WizardInput): TrainGate {
  if (!canTrain) {
    const yours = role ? ` Your role (${role}) cannot start training.` : "";
    return {
      status: "forbidden",
      reason: `Training a model needs the train-models permission, which the analyst and admin roles have.${yours} Ask an analyst or an admin to train a new version. Forecasts keep working without it: statistical methods are used until a trained model passes the quality gate.`,
    };
  }
  if (!dataset) {
    return { status: "locked", reason: "Upload a sales file in step 1, or open a report from the upload history, first." };
  }
  if (dataset.status !== "imported") {
    return {
      status: "locked",
      reason: "This file was rejected, so it added no data to train on. Fix the problems listed in its quality report and upload it again.",
    };
  }
  if (!reviewed) {
    return { status: "locked", reason: "Review the quality report in step 2, then choose “Continue to training”." };
  }
  return { status: "ready", reason: null };
}

export type StepState = "done" | "current" | "upcoming" | "unavailable";

export interface WizardStep {
  key: "upload" | "review" | "train";
  label: string;
  state: StepState;
}

export function wizardSteps(input: WizardInput & { trainingFinished?: boolean }): WizardStep[] {
  const { dataset, reviewed } = input;
  const gate = trainGate(input);
  const imported = dataset?.status === "imported";
  return [
    { key: "upload", label: "Upload sales history", state: imported ? "done" : "current" },
    { key: "review", label: "Review data quality", state: !imported ? "upcoming" : reviewed ? "done" : "current" },
    {
      key: "train",
      label: "Train a model",
      state: gate.status === "forbidden" ? "unavailable" : gate.status === "locked" ? "upcoming" : input.trainingFinished ? "done" : "current",
    },
  ];
}
