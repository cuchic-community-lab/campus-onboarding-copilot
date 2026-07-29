import argparse
import json
from pathlib import Path

from .config import DB_PATH
from .chat import GroundedChatService
from .context_builder import build_context_packet
from .evaluation import evaluate, evaluate_chat
from .retrieval import HybridRetriever
from .server import serve
from .service import audit_corpus, build_knowledge_base, corpus_stats
from .sync import sync_corpus


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="campus-copilot")
    sub = parser.add_subparsers(dest="command", required=True)
    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--all-files", action="store_true")
    sub.add_parser("build")
    query_parser = sub.add_parser("query")
    query_parser.add_argument("query")
    query_parser.add_argument("--cohort")
    query_parser.add_argument("--major")
    query_parser.add_argument("--student-level", default="undergraduate", choices=["undergraduate", "graduate", "all"])
    query_parser.add_argument("--top-k", type=int, default=6)
    query_parser.add_argument("--context", action="store_true")
    chat_parser = sub.add_parser("chat")
    chat_parser.add_argument("query")
    chat_parser.add_argument("--cohort")
    chat_parser.add_argument("--major")
    chat_parser.add_argument("--student-level", default="undergraduate", choices=["undergraduate", "graduate", "all"])
    chat_parser.add_argument("--top-k", type=int, default=6)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    sub.add_parser("audit")
    sub.add_parser("stats")
    sub.add_parser("evaluate")
    sub.add_parser("evaluate-chat")
    args = parser.parse_args()

    if args.command == "sync":
        _print(sync_corpus(args.all_files))
    elif args.command == "build":
        _print(build_knowledge_base())
    elif args.command == "query":
        profile = {key: value for key, value in {"cohort": args.cohort, "major": args.major, "student_level": args.student_level}.items() if value}
        result = HybridRetriever(DB_PATH).search(args.query, args.top_k, profile)
        _print(build_context_packet(result) if args.context else result)
    elif args.command == "chat":
        profile = {key: value for key, value in {"cohort": args.cohort, "major": args.major, "student_level": args.student_level}.items() if value}
        _print(GroundedChatService(HybridRetriever(DB_PATH)).ask(args.query, profile=profile, top_k=args.top_k))
    elif args.command == "serve":
        serve(args.host, args.port)
    elif args.command == "audit":
        _print(audit_corpus())
    elif args.command == "stats":
        _print(corpus_stats())
    elif args.command == "evaluate":
        _print(evaluate())
    elif args.command == "evaluate-chat":
        _print(evaluate_chat())


if __name__ == "__main__":
    main()
