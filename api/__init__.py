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

    app.register_blueprint(bp_kline)
    app.register_blueprint(bp_board)
    app.register_blueprint(bp_search)
    app.register_blueprint(bp_signal_ai)
    app.register_blueprint(bp_system)
    app.register_blueprint(bp_ctx)

