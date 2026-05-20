"""Train a linear probe on cached DINOv2 embeddings for one subset.

Loads artifacts/embeddings_<subset>.npz, fits sklearn LogisticRegression,
saves the classifier to artifacts/classifier_<subset>.pkl plus a report.
"""
from pathlib import Path
import argparse
import numpy as np
import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.metrics import classification_report, confusion_matrix
import json
import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subset", required=True,
                        choices=["occupancy", "furnished", "empty"])
    args = parser.parse_args()

    emb_path = ARTIFACTS / f"embeddings_{args.subset}.npz"
    clf_path = ARTIFACTS / f"classifier_{args.subset}.pkl"
    names_path = ARTIFACTS / f"class_names_{args.subset}.json"
    report_path = ARTIFACTS / f"training_report_{args.subset}.txt"

    if not emb_path.exists():
        print(f"{emb_path} not found. Run extract_embeddings.py --subset {args.subset} first.")
        return

    print(f"Loading embeddings from {emb_path}...")
    data = np.load(emb_path, allow_pickle=True)
    X, y = data["X"], data["y"]
    class_names = list(data["class_names"])
    print(f"   X: {X.shape}, y: {y.shape}, {len(class_names)} classes\n")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.15, random_state=42, stratify=y
    )
    print(f"Train: {X_train.shape[0]}, Test: {X_test.shape[0]}\n")

    print("Training linear probe (LogisticRegression, multinomial, L2)...")
    clf = LogisticRegression(
        solver="lbfgs",
        C=1.0,
        max_iter=2000,
        class_weight="balanced",
        random_state=42,
    )
    clf.fit(X_train, y_train)

    print("\n5-fold CV on training portion...")
    cv_scores = cross_val_score(
        LogisticRegression(solver="lbfgs", C=1.0, max_iter=2000,
                           class_weight="balanced", random_state=42),
        X_train, y_train, cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42),
        scoring="accuracy", n_jobs=-1,
    )
    print(f"   CV accuracy: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

    test_acc = clf.score(X_test, y_test)
    print(f"\nHeld-out test accuracy: {test_acc:.3f}")

    y_pred = clf.predict(X_test)
    report = classification_report(y_test, y_pred, target_names=class_names, zero_division=0)
    print("\n=== Classification report (test set) ===")
    print(report)

    cm = confusion_matrix(y_test, y_pred)
    print("=== Confusion matrix (rows=true, cols=pred) ===")
    name_w = max(len(n) for n in class_names) + 1
    header = " " * name_w + " ".join(f"{n[:7]:>8}" for n in class_names)
    print(header)
    for i, name in enumerate(class_names):
        row = " ".join(f"{v:>8}" for v in cm[i])
        print(f"{name:<{name_w}}{row}")

    joblib.dump(clf, clf_path)
    names_path.write_text(json.dumps({i: n for i, n in enumerate(class_names)}, indent=2))
    report_path.write_text(
        f"Subset: {args.subset}\n"
        f"CV accuracy: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}\n"
        f"Test accuracy: {test_acc:.3f}\n\n"
        f"{report}\n"
        f"Confusion matrix (rows=true, cols=pred):\n"
        f"{cm}\n"
    )

    print(f"\nSaved:\n   {clf_path}\n   {names_path}\n   {report_path}")


if __name__ == "__main__":
    main()
