# Render the game's native Spine assets in an isolated project, without saves,
# game autoloads, a run, or the gameplay bridge. Output is local static artwork.
extends SceneTree

func _initialize():
	call_deferred("export_portraits")

func export_portraits():
	print("Artwork project: ", ProjectSettings.get_setting("application/config/name"))
	var args = OS.get_cmdline_user_args()
	if args.size() == 3 and args[0] == "--pack":
		var packer = PCKPacker.new()
		if packer.pck_start(args[1]) != OK:
			quit(4)
			return
		var files = JSON.parse_string(FileAccess.get_file_as_string(args[2]))
		for file in files:
			if packer.add_file(file.resource, file.source) != OK:
				quit(5)
				return
		quit(packer.flush())
		return
	if args.size() != 1:
		quit(2)
		return
	if ProjectSettings.get_setting("application/config/name") != "AutoSlay artwork export":
		push_error("Refusing to render outside the isolated artwork pack")
		quit(6)
		return
	var jobs = JSON.parse_string(FileAccess.get_file_as_string(args[0]))
	var results = []
	for job in jobs:
		var viewport = SubViewport.new()
		viewport.size = Vector2i(384, 384)
		viewport.transparent_bg = true
		viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
		root.add_child(viewport)
		var sprite = ClassDB.instantiate("SpineSprite")
		if sprite == null:
			quit(3)
			return
		sprite.set("skeleton_data_res", load(job.resource))
		viewport.add_child(sprite)
		await process_frame
		var skeleton = sprite.call("get_skeleton")
		if job.get("skins", []).size() > 0:
			var combined = sprite.call("new_skin", "encyclopedia-preview")
			for skin_name in job.skins:
				combined.call("add_skin", skeleton.call("get_data").call("find_skin", skin_name))
			skeleton.call("set_skin", combined)
			skeleton.call("set_slots_to_setup_pose")
		elif job.get("skin", "") != "":
			skeleton.call("set_skin_by_name", job.skin)
			skeleton.call("set_slots_to_setup_pose")
		if job.get("animation", "") != "":
			sprite.call("get_animation_state").call("set_animation", job.animation, true, 0)
		for track in job.get("tracks", []):
			sprite.call("get_animation_state").call("set_animation", track.animation, true, track.track)
		await process_frame
		var bounds: Rect2 = skeleton.call("get_bounds")
		if bounds.size.x <= 0 or bounds.size.y <= 0:
			results.append({"entity_id": job.entity_id, "error": "empty_bounds"})
			viewport.queue_free()
			continue
		var zoom = min(352.0 / bounds.size.x, 352.0 / bounds.size.y)
		sprite.scale = Vector2(zoom, zoom)
		sprite.position = Vector2(192, 192) - (bounds.position + bounds.size / 2) * zoom
		await process_frame
		await RenderingServer.frame_post_draw
		var image = viewport.get_texture().get_image()
		var used = image.get_used_rect()
		if used.size.x > 0 and used.size.y > 0:
			image = image.get_region(used.grow(4).intersection(Rect2i(0, 0, 384, 384)))
			var error = image.save_png(job.output)
			results.append({"entity_id": job.entity_id, "error": error})
		else:
			results.append({"entity_id": job.entity_id, "error": "empty_image"})
		viewport.queue_free()
		await process_frame
	print(JSON.stringify(results))
	quit()
