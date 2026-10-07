# vulture's known false positives (`make test-dead-code`): parameters that callback and
# framework signatures must accept even when the body doesn't read them.
aggregator  # Pipecat event handlers: on_user_turn_stopped(aggregator, strategy, message)
callback  # FrameProcessor.push_frame(frame, direction, callback)
publication  # LiveKit track_subscribed(track, publication, participant)
vad_mode  # bot._turn_detection_for_vad_mode: stt_vad_mode is a dead knob (always "local"); goes with its column in the Bot settings cleanup
