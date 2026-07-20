"""
api/__init__.py — 路由注册中心
"""
from flask import Flask


def register_routes(app: Flask):
    from api.kline_routes import bp as bp_kline
    from api.board_routes import bp as bp_board
    from api.search_routes import bp as bp_search
    from api.signal_ai_routes import bp as bp_signal_ai
    from api.system_routes import bp as bp_system
    from api.ctx_route import bp as bp_ctx
    from api.mcp_routes import mcp_api
    from api.stream_routes import stream_api
    from api.annotation_routes import bp as bp_annotation
    from api.session_routes import bp as bp_session

    app.register_blueprint(bp_kline)
    app.register_blueprint(bp_board)
    app.register_blueprint(bp_search)
    app.register_blueprint(bp_signal_ai)
    app.register_blueprint(bp_system)
    app.register_blueprint(bp_ctx)
    app.register_blueprint(mcp_api)
    app.register_blueprint(stream_api)
    app.register_blueprint(bp_annotation)
    app.register_blueprint(bp_session)

