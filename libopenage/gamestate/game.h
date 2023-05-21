// Copyright 2018-2023 the openage authors. See copying.md for legal info.

#pragma once

#include <memory>

#include "util/path.h"

namespace nyan {
class Database;
}

namespace openage {

namespace event {
class EventLoop;
}

namespace renderer {
class RenderFactory;
}

namespace gamestate {
class GameState;
class Universe;

/**
 * Manages a game session (settings, win conditions, etc.).
 *
 * TODO: Create sensible structure of gamestate classes. Right now it's
 *
 *       Game->Universe|->World->GameEntity
 *                     |->Terrain
 *
 * 		which can be confusing.
 */
class Game {
public:
	/**
	 * Create a new game.
	 *
	 * @param root_dir openage root directory.
     * @param event_loop Event simulation loop for the gamestate.
	 */
	Game(const util::Path &root_dir,
	     const std::shared_ptr<openage::event::EventLoop> &event_loop);
	~Game() = default;

	/**
     * Get the current game state.
     */
	const std::shared_ptr<GameState> &get_state() const;

	/**
	 * Attach a renderer to the game which enables graphical display options for
	 * all ingame entities.
	 *
	 * @param render_factory Factory for creating connector objects for gamestate->renderer
	 *                       communication.
	 */
	void attach_renderer(const std::shared_ptr<renderer::RenderFactory> &render_factory);

private:
	/**
     * Nyan game data database.
     */
	std::shared_ptr<nyan::Database> db;

	/**
     * State of the current game.
     */
	std::shared_ptr<GameState> state;

	/**
	 * Object that controls entities in the game world.
	 */
	std::shared_ptr<Universe> universe;
};

} // namespace gamestate
} // namespace openage
