import argparse
import json
from pathlib import Path

from .config import DB_PATH, ensure_dirs, load_env
from .chat import GroundedChatService
from .context_builder import build_context_packet
from .retrieval import HybridRetriever
from .server import serve
from .service import audit_corpus, build_knowledge_base, corpus_stats


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    load_env()
    ensure_dirs()
    parser = argparse.ArgumentParser(prog="xiaohaigpt")
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--force", action="store_true", help="force full rebuild")
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
    serve_parser.add_argument("--no-build", action="store_true", help="skip auto index build on startup")
    ingest_parser = sub.add_parser("ingest")
    ingest_parser.add_argument("--force", action="store_true")
    sub.add_parser("audit")
    sub.add_parser("stats")
    args = parser.parse_args()

    if args.command == "build":
        _print(build_knowledge_base(DB_PATH, force=args.force))
    elif args.command == "query":
        profile = {key: value for key, value in {"cohort": args.cohort, "major": args.major, "student_level": args.student_level}.items() if value}
        result = HybridRetriever(DB_PATH).search(args.query, args.top_k, profile)
        _print(build_context_packet(result) if args.context else result)
    elif args.command == "chat":
        profile = {key: value for key, value in {"cohort": args.cohort, "major": args.major, "student_level": args.student_level}.items() if value}
        _print(GroundedChatService(HybridRetriever(DB_PATH)).ask(args.query, profile=profile, top_k=args.top_k))
    elif args.command == "serve":
        serve(args.host, args.port, auto_build=not args.no_build)
    elif args.command == "ingest":
        _print(build_knowledge_base(DB_PATH, force=args.force))
    elif args.command == "audit":
        _print(audit_corpus())
    elif args.command == "stats":
        _print(corpus_stats(DB_PATH))


if __name__ == "__main__":
    main()
