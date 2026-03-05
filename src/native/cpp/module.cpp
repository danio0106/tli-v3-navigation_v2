#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cmath>
#include <cstdint>
#include <chrono>
#include <deque>
#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace py = pybind11;

class NativeScanner {
public:
    NativeScanner(py::object memory,
                  py::object addresses,
                  py::object progress_callback,
                  py::object backend_scanner = py::none())
        : memory_(std::move(memory)),
          addresses_(std::move(addresses)),
          progress_callback_(std::move(progress_callback)),
          backend_scanner_(std::move(backend_scanner)) {
                _load_constants();
                overlay_snapshot_ = _empty_overlay_snapshot();
    }

    ~NativeScanner() {
        stop_overlay_worker();
    }

    py::object scan_dump_chain(py::object use_cache = py::bool_(true)) {
        return call_backend0("scan_dump_chain", std::move(use_cache));
    }

    py::object scan_fnamepool(py::object module_base = py::int_(0), py::object module_size = py::int_(0)) {
        return call_backend0("scan_fnamepool", std::move(module_base), std::move(module_size));
    }

    py::object scan_gobjects(py::object module_base = py::int_(0), py::object module_size = py::int_(0)) {
        return call_backend0("scan_gobjects", std::move(module_base), std::move(module_size));
    }

    void set_cached_gworld_static(py::object value) {
        cached_gworld_static_ = std::move(value);
        call_backend0("set_cached_gworld_static", cached_gworld_static_);
    }

    void clear_fightmgr_cache() { call_backend0("clear_fightmgr_cache"); }

    void set_nav_collision_probe(py::object enabled, py::object interval_s = py::float_(2.0)) {
        call_backend0("set_nav_collision_probe", std::move(enabled), std::move(interval_s));
    }

    py::object check_chain_valid() { return call_backend0("check_chain_valid"); }

    py::object get_gworld_ptr() { return call_backend0("get_gworld_ptr"); }

    py::object read_player_xy() {
        // Phase-B first native slice: use AddressManager chains directly for
        // hot-path XY reads, with backend fallback for resilience.
        py::object x_obj = read_chain_value("player_x");
        py::object y_obj = read_chain_value("player_y");
        if (!x_obj.is_none() && !y_obj.is_none()) {
            try {
                return py::make_tuple(py::float_(x_obj), py::float_(y_obj));
            } catch (const py::error_already_set&) {
                // Fallback to backend path below if conversion fails.
            }
        }
        return call_backend0("read_player_xy");
    }

    py::object read_zone_name() {
        // Native fast path: GWorld UObject FName resolution via FNamePool.
        // Falls back to backend for compatibility/log behavior on failures.
        try {
            const std::uint64_t fnp = fnamepool_addr();
            const std::uint64_t gworld = get_gworld_ptr_cached();
            if (!fnp || !is_valid_ptr(gworld) || !memory_is_ready()) {
                return py::str("");
            }

            const int fname_index = read_int(gworld + 0x18ull);
            if (fname_index <= 0) {
                return py::str("");
            }

            std::string zone;
            try {
                zone = py::str(memory_.attr("read_fname")(fnp, fname_index));
            } catch (const py::error_already_set&) {
                zone.clear();
            }

            if (!zone.empty() && zone.find("UIMain") == std::string::npos) {
                last_real_zone_name_ = zone;
            }
            return py::str(zone);
        } catch (const py::error_already_set&) {
            return call_backend0("read_zone_name");
        }
    }

    py::object read_real_zone_name() {
        try {
            const std::string zone = py::str(read_zone_name());
            if (!zone.empty() && zone.find("UIMain") != std::string::npos) {
                return py::str(last_real_zone_name_);
            }
            return py::str(zone);
        } catch (const py::error_already_set&) {
            return call_backend0("read_real_zone_name");
        }
    }
    py::object get_typed_events() {
        // Native fast path for high-frequency polling. Preserve EventInfo-like
        // shape used by Python callers; fall back to backend on failures.
        try {
            if (!memory_is_ready()) {
                return py::list();
            }

            const std::uint64_t fightmgr = get_fightmgr_ptr_cached();
            if (!is_valid_ptr(fightmgr) || fightmgr_map_gameplay_offset_ <= 0 || fightmgr_map_customtrap_offset_ <= 0) {
                return call_backend0("get_typed_events");
            }

            std::vector<RawEvent> gameplay = read_tmap_events(fightmgr, fightmgr_map_gameplay_offset_);
            if (gameplay.empty()) {
                return py::list();
            }
            std::vector<RawEvent> traps = read_tmap_events(fightmgr, fightmgr_map_customtrap_offset_);

            std::vector<std::pair<double, double>> carjack_vehicle_positions;
            std::vector<std::uint64_t> carjack_vehicle_entities;
            std::vector<std::pair<double, double>> sandlord_platform_positions;

            const std::uint64_t fnp = fnamepool_addr();
            if (!fnp) {
                return call_backend0("get_typed_events");
            }
            const double tol = carjack_pos_tolerance_ > 0.0 ? carjack_pos_tolerance_ : 200.0;

            for (const auto& tr : traps) {
                if (std::fabs(tr.x) < 1.0 && std::fabs(tr.y) < 1.0) {
                    continue;
                }
                std::string cls_name;
                if (fnp) {
                    const std::uint64_t class_ptr = read_ulong(tr.address + 0x10ull);
                    if (is_valid_ptr(class_ptr)) {
                        try {
                            cls_name = py::str(memory_.attr("read_uobject_name")(fnp, class_ptr));
                        } catch (const py::error_already_set&) {
                            cls_name.clear();
                        }
                    }
                }
                if (!cls_name.empty() && cls_name.find("TrapS") != std::string::npos) {
                    carjack_vehicle_positions.emplace_back(tr.x, tr.y);
                    carjack_vehicle_entities.push_back(tr.address);
                } else if (cls_name == "EMapCustomTrap") {
                    sandlord_platform_positions.emplace_back(tr.x, tr.y);
                }
            }

            py::object ns_ctor = py::module_::import("types").attr("SimpleNamespace");
            py::list out;

            bool sandlord_assigned = false;
            bool backend_carjack_set = false;
            bool native_carjack_set = false;
            for (const auto& ev : gameplay) {
                bool is_target = false;
                std::string ev_type = "Unknown";
                double final_x = ev.x;
                double final_y = ev.y;
                std::uint64_t vehicle_addr = 0;

                if (std::fabs(ev.x) > 1.0 || std::fabs(ev.y) > 1.0) {
                    for (std::size_t i = 0; i < carjack_vehicle_positions.size(); ++i) {
                        const auto& p = carjack_vehicle_positions[i];
                        if (std::fabs(ev.x - p.first) <= tol && std::fabs(ev.y - p.second) <= tol) {
                            ev_type = "Carjack";
                            is_target = true;
                            vehicle_addr = i < carjack_vehicle_entities.size() ? carjack_vehicle_entities[i] : 0;
                            if (!backend_carjack_set && !backend_scanner_.is_none()) {
                                try {
                                    backend_scanner_.attr("_carjack_truck_pos") = py::make_tuple(p.first, p.second);
                                } catch (const py::error_already_set&) {
                                }
                                if (vehicle_addr) {
                                    try {
                                        backend_scanner_.attr("_carjack_vehicle_addr") = py::int_(vehicle_addr);
                                    } catch (const py::error_already_set&) {
                                    }
                                }
                                backend_carjack_set = true;
                            }
                            if (!native_carjack_set) {
                                carjack_truck_valid_ = true;
                                carjack_truck_x_ = p.first;
                                carjack_truck_y_ = p.second;
                                carjack_vehicle_addr_ = vehicle_addr;
                                native_carjack_set = true;
                            }
                            break;
                        }
                    }
                } else if (!sandlord_assigned && !sandlord_platform_positions.empty()) {
                    ev_type = "Sandlord";
                    is_target = true;
                    final_x = sandlord_platform_positions[0].first;
                    final_y = sandlord_platform_positions[0].second;
                    sandlord_assigned = true;
                }

                py::dict kwargs;
                kwargs["address"] = py::int_(ev.address);
                kwargs["event_type"] = py::str(ev_type);
                kwargs["cfg_id"] = py::int_(ev.cfg_id);
                kwargs["cfg_type"] = py::int_(0);
                kwargs["cfg_extend_id"] = py::int_(0);
                kwargs["position"] = py::make_tuple(final_x, final_y, ev.z);
                kwargs["is_target_event"] = py::bool_(is_target);
                kwargs["ecfg_address"] = py::int_(0);
                kwargs["sub_object_name"] = py::str("");
                kwargs["sub_object_class"] = py::str("");
                kwargs["wave_counter"] = py::int_(ev.wave_counter);
                kwargs["spawn_index"] = py::int_(ev.spawn_index);
                kwargs["bvalid"] = py::int_(ev.bvalid);
                kwargs["abp_class"] = py::str("");
                kwargs["source_type"] = py::int_(-1);
                kwargs["monster_point_id"] = py::int_(-1);
                kwargs["carjack_vehicle_addr"] = py::int_(vehicle_addr);
                kwargs["carjack_cur_status_index"] = py::int_(-1);
                kwargs["carjack_cur_status"] = py::int_(-1);
                kwargs["carjack_trap_execute_state"] = py::int_(-1);
                kwargs["carjack_wait_time"] = py::float_(-1.0);
                kwargs["carjack_hit_count"] = py::int_(-1);
                kwargs["carjack_work_count"] = py::int_(-1);
                kwargs["carjack_max_work_count"] = py::int_(-1);
                kwargs["carjack_skill_index"] = py::int_(-1);
                kwargs["carjack_trigger_index"] = py::int_(-1);
                kwargs["carjack_player_enter"] = py::int_(-1);
                out.append(ns_ctor(**kwargs));
            }

            const double now_s = now_seconds();
            if (native_carjack_set) {
                carjack_active_until_ = now_s + 2.0;
                if (carjack_active_since_ <= 0.0) {
                    carjack_active_since_ = now_s;
                    guard_seed_count_ = 0;
                    guard_seed_addrs_.clear();
                    entity_pos_history_.clear();
                    entity_first_seen_t_.clear();
                }
            } else if (now_s > carjack_active_until_) {
                carjack_active_since_ = 0.0;
                guard_seed_count_ = 0;
                guard_seed_addrs_.clear();
                entity_pos_history_.clear();
                entity_first_seen_t_.clear();
            }

            if (!native_carjack_set) {
                carjack_truck_valid_ = false;
                carjack_vehicle_addr_ = 0;
            }

            return out;
        } catch (const py::error_already_set&) {
            return call_backend0("get_typed_events");
        }
    }
    py::object get_monster_entities() {
        // Native fast path: read FightMgr.MapRoleMonster and expose EventInfo-like
        // records used by overlay/entity views. Falls back to backend on failures.
        try {
            if (!memory_is_ready()) {
                return py::list();
            }
            const std::uint64_t fightmgr = get_fightmgr_ptr_cached();
            if (!is_valid_ptr(fightmgr) || fightmgr_map_monster_offset_ <= 0) {
                return call_backend0("get_monster_entities");
            }

            std::vector<RawEvent> monsters = read_tmap_events(fightmgr, fightmgr_map_monster_offset_);
            py::list out;
            py::object ns_ctor = py::module_::import("types").attr("SimpleNamespace");

            const std::uint64_t fnp = fnamepool_addr();
            for (const auto& m : monsters) {
                std::string cls_name;
                if (fnp) {
                    const std::uint64_t class_ptr = read_ulong(m.address + 0x10ull);
                    if (is_valid_ptr(class_ptr)) {
                        try {
                            cls_name = py::str(memory_.attr("read_uobject_name")(fnp, class_ptr));
                        } catch (const py::error_already_set&) {
                            cls_name.clear();
                        }
                    }
                }

                py::dict kwargs;
                kwargs["address"] = py::int_(m.address);
                kwargs["event_type"] = py::str("Monster");
                kwargs["cfg_id"] = py::int_(m.cfg_id);
                kwargs["cfg_type"] = py::int_(0);
                kwargs["cfg_extend_id"] = py::int_(0);
                kwargs["position"] = py::make_tuple(m.x, m.y, m.z);
                kwargs["is_target_event"] = py::bool_(false);
                kwargs["ecfg_address"] = py::int_(0);
                kwargs["sub_object_name"] = py::str("");
                kwargs["sub_object_class"] = py::str(cls_name);
                kwargs["wave_counter"] = py::int_(m.wave_counter);
                kwargs["spawn_index"] = py::int_(m.spawn_index);
                kwargs["bvalid"] = py::int_(m.bvalid);
                kwargs["abp_class"] = py::str("");
                kwargs["source_type"] = py::int_(-1);
                kwargs["monster_point_id"] = py::int_(-1);
                kwargs["carjack_vehicle_addr"] = py::int_(0);
                kwargs["carjack_cur_status_index"] = py::int_(-1);
                kwargs["carjack_cur_status"] = py::int_(-1);
                kwargs["carjack_trap_execute_state"] = py::int_(-1);
                kwargs["carjack_wait_time"] = py::float_(-1.0);
                kwargs["carjack_hit_count"] = py::int_(-1);
                kwargs["carjack_work_count"] = py::int_(-1);
                kwargs["carjack_max_work_count"] = py::int_(-1);
                kwargs["carjack_skill_index"] = py::int_(-1);
                kwargs["carjack_trigger_index"] = py::int_(-1);
                kwargs["carjack_player_enter"] = py::int_(-1);
                out.append(ns_ctor(**kwargs));
            }

            return out;
        } catch (const py::error_already_set&) {
            return call_backend0("get_monster_entities");
        }
    }

    py::object count_nearby_monsters(py::object x, py::object y, py::object radius = py::float_(2500.0)) {
        // Native fast path: read FightMgr.MapRoleMonster TMap and count nearby alive entities.
        // Falls back to backend scanner on any read/parsing error.
        try {
            const double px = py::float_(x);
            const double pyv = py::float_(y);
            const double r = py::float_(radius);
            if (r <= 0.0 || !memory_is_ready()) {
                return py::int_(0);
            }

            const std::uint64_t fightmgr = get_fightmgr_ptr_cached();
            if (!is_valid_ptr(fightmgr) || fightmgr_map_monster_offset_ <= 0) {
                return call_backend0("count_nearby_monsters", std::move(x), std::move(y), std::move(radius));
            }

            const std::uint64_t tmap_addr = fightmgr + static_cast<std::uint64_t>(fightmgr_map_monster_offset_);
            const std::uint64_t data_ptr = read_ulong(tmap_addr + 0x0);
            const int array_num = read_int(tmap_addr + 0x8);
            if (!is_valid_ptr(data_ptr) || array_num <= 0 || array_num > 50000) {
                return py::int_(0);
            }

            const double r2 = r * r;
            int nearby = 0;
            const int n = std::min(array_num, 50000);
            for (int i = 0; i < n; ++i) {
                const std::uint64_t elem = data_ptr + static_cast<std::uint64_t>(i) * 0x18ull;
                const std::uint64_t ent = read_ulong(elem + 0x8);
                if (!is_valid_ptr(ent)) {
                    continue;
                }

                const int bvalid = read_byte(ent + 0x720);
                if (bvalid == 0) {
                    continue;
                }

                double ex = 0.0, ey = 0.0, ez = 0.0;
                if (!read_actor_position(ent, ex, ey, ez)) {
                    continue;
                }
                const double dx = ex - px;
                const double dy = ey - pyv;
                if ((dx * dx + dy * dy) <= r2) {
                    nearby += 1;
                }
            }
            return py::int_(nearby);
        } catch (const py::error_already_set&) {
            return call_backend0("count_nearby_monsters", std::move(x), std::move(y), std::move(radius));
        }
    }

    py::object get_carjack_truck_position() {
        if (carjack_truck_valid_) {
            return py::make_tuple(carjack_truck_x_, carjack_truck_y_);
        }
        try {
            py::object _ = get_typed_events();
            (void)_;
        } catch (const py::error_already_set&) {
        }
        if (carjack_truck_valid_) {
            return py::make_tuple(carjack_truck_x_, carjack_truck_y_);
        }
        return call_backend0("get_carjack_truck_position");
    }

    py::object get_carjack_guard_positions() {
        // Native flee-speed guard tracking mirror (v4.65-style).
        try {
            if (!carjack_truck_valid_) {
                return py::list();
            }
            const double now_s = now_seconds();
            if (now_s > (carjack_active_until_ + 2.0)) {
                return py::list();
            }

            const double tx = carjack_truck_x_;
            const double ty = carjack_truck_y_;
            py::object mons_obj = get_monster_entities();
            py::list mons = py::list(mons_obj);

            for (auto item : mons) {
                py::object m = py::reinterpret_borrow<py::object>(item);
                const std::uint64_t addr = attr_u64(m, "address", 0);
                if (!addr) {
                    continue;
                }
                const int bvalid = attr_i(m, "bvalid", 0);
                if (bvalid == 0) {
                    continue;
                }
                py::tuple pos = attr_tuple3(m, "position");
                if (pos.size() < 2) {
                    continue;
                }
                const double mx = py::float_(pos[0]);
                const double my = py::float_(pos[1]);

                auto& hist = entity_pos_history_[addr];
                hist.push_back(PosSample{now_s, mx, my});
                while (hist.size() > 16) {
                    hist.pop_front();
                }
                if (entity_first_seen_t_.find(addr) == entity_first_seen_t_.end()) {
                    entity_first_seen_t_[addr] = now_s;
                }

                const double dx = mx - tx;
                const double dy = my - ty;
                const double dist_truck = std::sqrt(dx * dx + dy * dy);
                if (guard_seed_count_ < guard_seed_max_
                    && dist_truck <= 2500.0
                    && carjack_active_since_ > 0.0
                    && now_s < (carjack_active_since_ + guard_seed_window_s_)
                    && guard_seed_addrs_.find(addr) == guard_seed_addrs_.end()) {
                    guard_seed_addrs_.insert(addr);
                    guard_seed_count_ += 1;
                }
            }

            py::list out;
            for (const auto& kv : entity_pos_history_) {
                const std::uint64_t addr = kv.first;
                const auto& hist = kv.second;
                if (hist.size() < 2) {
                    continue;
                }
                const auto& oldest = hist.front();
                const auto& newest = hist.back();
                const double dt = newest.t - oldest.t;
                if (dt < 0.01) {
                    continue;
                }
                const double vx = newest.x - oldest.x;
                const double vy = newest.y - oldest.y;
                const double speed = std::sqrt(vx * vx + vy * vy) / dt;
                const double first_seen = map_get(entity_first_seen_t_, addr, newest.t);
                const double survived = newest.t - first_seen;
                const bool is_seed = guard_seed_addrs_.find(addr) != guard_seed_addrs_.end();
                const bool include = is_seed || (speed >= guard_flee_min_speed_ && survived >= guard_min_survive_s_);
                if (!include) {
                    continue;
                }
                const double dx = newest.x - tx;
                const double dy = newest.y - ty;
                const double dist_truck = std::sqrt(dx * dx + dy * dy);
                if (dist_truck > 12000.0) {
                    continue;
                }

                py::dict g;
                g["x"] = py::float_(newest.x);
                g["y"] = py::float_(newest.y);
                g["addr"] = py::int_(addr);
                g["abp"] = py::str("");
                g["dist_truck"] = py::float_(dist_truck);
                out.append(g);
            }

            if (!out.empty()) {
                return out;
            }

            py::dict fallback;
            fallback["x"] = py::float_(tx);
            fallback["y"] = py::float_(ty);
            fallback["addr"] = py::int_(0);
            fallback["abp"] = py::str("truck_fallback");
            fallback["dist_truck"] = py::float_(0.0);
            py::list single;
            single.append(fallback);
            return single;
        } catch (const py::error_already_set&) {
            return call_backend0("get_carjack_guard_positions");
        }
    }

    py::object get_nearby_interactive_items(py::object x,
                                            py::object y,
                                            py::object radius = py::float_(3000.0),
                                            py::object require_valid = py::bool_(true)) {
        // Native fast path: read FightMgr.MapInteractiveItem TMap and return
        // lightweight attribute objects compatible with existing Python callers.
        try {
            const double px = py::float_(x);
            const double pyv = py::float_(y);
            const double r = py::float_(radius);
            const bool require_valid_flag = py::bool_(require_valid);
            if (r <= 0.0 || !memory_is_ready()) {
                return py::list();
            }

            const std::uint64_t fightmgr = get_fightmgr_ptr_cached();
            if (!is_valid_ptr(fightmgr) || fightmgr_map_interactive_offset_ <= 0) {
                return call_backend0("get_nearby_interactive_items", std::move(x), std::move(y), std::move(radius), std::move(require_valid));
            }

            const std::uint64_t tmap_addr = fightmgr + static_cast<std::uint64_t>(fightmgr_map_interactive_offset_);
            const std::uint64_t data_ptr = read_ulong(tmap_addr + 0x0);
            const int array_num = read_int(tmap_addr + 0x8);
            if (!is_valid_ptr(data_ptr) || array_num <= 0 || array_num > 50000) {
                return py::list();
            }

            py::list out;
            const double r2 = r * r;
            const std::uint64_t fnp = fnamepool_addr();
            py::object ns_ctor = py::module_::import("types").attr("SimpleNamespace");

            const int n = std::min(array_num, 50000);
            for (int i = 0; i < n; ++i) {
                const std::uint64_t elem = data_ptr + static_cast<std::uint64_t>(i) * 0x18ull;
                const std::uint64_t ent = read_ulong(elem + 0x8);
                if (!is_valid_ptr(ent)) {
                    continue;
                }

                const int bvalid = read_byte(ent + 0x720);
                if (require_valid_flag && bvalid == 0) {
                    continue;
                }

                double ex = 0.0, ey = 0.0, ez = 0.0;
                if (!read_actor_position(ent, ex, ey, ez)) {
                    continue;
                }
                const double dx = ex - px;
                const double dy = ey - pyv;
                if ((dx * dx + dy * dy) > r2) {
                    continue;
                }

                std::string cls_name;
                if (fnp) {
                    const std::uint64_t class_ptr = read_ulong(ent + 0x10);
                    if (is_valid_ptr(class_ptr)) {
                        try {
                            cls_name = py::str(memory_.attr("read_uobject_name")(fnp, class_ptr));
                        } catch (const py::error_already_set&) {
                            cls_name.clear();
                        }
                    }
                }

                py::dict kwargs;
                kwargs["address"] = py::int_(ent);
                kwargs["position"] = py::make_tuple(ex, ey, ez);
                kwargs["bvalid"] = py::int_(bvalid);
                // Existing callers use these fields for strongbox heuristics.
                kwargs["sub_object_class"] = py::str(cls_name);
                kwargs["sub_object_name"] = py::str(cls_name);
                out.append(ns_ctor(**kwargs));
            }
            return out;
        } catch (const py::error_already_set&) {
            return call_backend0("get_nearby_interactive_items", std::move(x), std::move(y), std::move(radius), std::move(require_valid));
        }
    }

    py::object scan_boss_room() {
        // Native fast path mirroring scanner.py semantics.
        try {
            const std::uint64_t gobj = gobjects_addr();
            const std::uint64_t fnp = fnamepool_addr();
            if (!gobj || !fnp || memory_.is_none() || !py::hasattr(memory_, "find_gobjects_by_class_name")) {
                return py::none();
            }

            py::object actors = memory_.attr("find_gobjects_by_class_name")(gobj, fnp, py::str("MapBossRoom"));
            if (actors.is_none()) {
                return py::none();
            }

            py::list lst = py::list(actors);
            if (lst.empty()) {
                return py::none();
            }

            py::tuple first = py::tuple(lst[0]);
            if (first.size() < 1) {
                return py::none();
            }

            const std::uint64_t actor = py::int_(first[0]);
            if (!is_valid_ptr(actor)) {
                return py::none();
            }

            double x = 0.0, y = 0.0, z = 0.0;
            if (!read_actor_position(actor, x, y, z)) {
                return py::none();
            }
            if (std::fabs(x) < 1.0 && std::fabs(y) < 1.0) {
                return py::none();
            }
            return py::make_tuple(x, y);
        } catch (const py::error_already_set&) {
            return call_backend0("scan_boss_room");
        }
    }
    py::object read_minimap_visited_positions(py::object raw_zone_name = py::str("")) {
        return call_backend0("read_minimap_visited_positions", std::move(raw_zone_name));
    }
    py::object get_nav_collision_markers() {
        // Native accessor of backend nav-collision cache (faster than full Python method path).
        // Keeps existing probe/cache semantics while removing call indirection.
        try {
            if (!backend_scanner_.is_none() && py::hasattr(backend_scanner_, "_nav_collision_boxes")) {
                py::object boxes = backend_scanner_.attr("_nav_collision_boxes");
                if (!boxes.is_none()) {
                    return py::list(boxes);
                }
            }
            return py::list();
        } catch (const py::error_already_set&) {
            return call_backend0("get_nav_collision_markers");
        }
    }
    py::object get_fightmgr_ptr() { return call_backend0("get_fightmgr_ptr"); }

    py::object find_object_by_name(py::object name) {
        try {
            const std::uint64_t gobj = gobjects_addr();
            const std::uint64_t fnp = fnamepool_addr();
            if (!gobj || !fnp || memory_.is_none() || !py::hasattr(memory_, "find_gobject_by_name")) {
                return py::list();
            }
            return memory_.attr("find_gobject_by_name")(gobj, fnp, std::move(name));
        } catch (const py::error_already_set&) {
            return call_backend0("find_object_by_name", std::move(name));
        }
    }

    py::object read_player_hp() { return call_backend0("read_player_hp"); }

    py::int_ fnamepool_addr() const {
        if (backend_scanner_.is_none() || !py::hasattr(backend_scanner_, "fnamepool_addr")) {
            return py::int_(0);
        }
        try {
            return py::int_(backend_scanner_.attr("fnamepool_addr"));
        } catch (const py::error_already_set&) {
            return py::int_(0);
        }
    }

    py::int_ gobjects_addr() const {
        if (backend_scanner_.is_none() || !py::hasattr(backend_scanner_, "gobjects_addr")) {
            return py::int_(0);
        }
        try {
            return py::int_(backend_scanner_.attr("gobjects_addr"));
        } catch (const py::error_already_set&) {
            return py::int_(0);
        }
    }

    py::int_ _fnamepool_addr() const { return fnamepool_addr(); }
    py::int_ _gobjects_addr() const { return gobjects_addr(); }
    py::object _memory() const { return memory_; }
    py::object _scanner() const { return backend_scanner_; }

    py::object set_fnamepool_addr(py::object value) {
        return call_backend0("set_fnamepool_addr", std::move(value));
    }

    py::object _read_truck_guard_roster(py::object truck_addr, py::object fnamepool) {
        return call_backend0("_read_truck_guard_roster", std::move(truck_addr), std::move(fnamepool));
    }

    void start_overlay_worker(py::object interval_s = py::float_(0.20)) {
        double requested = 0.20;
        try {
            requested = py::float_(interval_s);
        } catch (const py::error_already_set&) {
            requested = 0.20;
        }
        if (!std::isfinite(requested) || requested < 0.05) {
            requested = 0.05;
        }
        if (requested > 1.0) {
            requested = 1.0;
        }

        stop_overlay_worker();
        overlay_interval_s_ = requested;
        overlay_stop_.store(false);
        overlay_worker_alive_.store(true);

        overlay_thread_ = std::thread([this]() {
            using clock = std::chrono::steady_clock;
            while (!overlay_stop_.load()) {
                const auto t0 = clock::now();
                try {
                    py::gil_scoped_acquire gil;
                    py::dict snap = _build_overlay_snapshot();
                    std::lock_guard<std::mutex> guard(overlay_mutex_);
                    overlay_snapshot_ = snap;
                } catch (const py::error_already_set&) {
                }

                const auto elapsed = std::chrono::duration<double>(clock::now() - t0).count();
                const double sleep_s = overlay_interval_s_ - elapsed;
                if (sleep_s > 0.001) {
                    std::this_thread::sleep_for(std::chrono::duration<double>(sleep_s));
                }
            }
            overlay_worker_alive_.store(false);
        });
    }

    void stop_overlay_worker() {
        overlay_stop_.store(true);
        if (overlay_thread_.joinable()) {
            overlay_thread_.join();
        }
        overlay_worker_alive_.store(false);
    }

    py::object get_overlay_snapshot() {
        std::lock_guard<std::mutex> guard(overlay_mutex_);
        if (overlay_snapshot_.is_none()) {
            return _empty_overlay_snapshot();
        }
        return py::dict(overlay_snapshot_);
    }

    bool overlay_worker_alive() const {
        return overlay_worker_alive_.load();
    }

    void cancel() {
        stop_overlay_worker();
        cancelled_ = true;
        call_backend0("cancel");
    }

private:
    struct RawEvent {
        std::uint64_t address = 0;
        int cfg_id = 0;
        int wave_counter = -1;
        int spawn_index = -1;
        int bvalid = -1;
        double x = 0.0;
        double y = 0.0;
        double z = 0.0;
    };

    struct PosSample {
        double t = 0.0;
        double x = 0.0;
        double y = 0.0;
    };

    void _load_constants() {
        try {
            py::module_ constants = py::module_::import("src.utils.constants");
            fightmgr_map_monster_offset_ = py::int_(constants.attr("FIGHTMGR_MAP_MONSTER_OFFSET"));
            fightmgr_map_interactive_offset_ = py::int_(constants.attr("FIGHTMGR_MAP_INTERACTIVE_OFFSET"));
            fightmgr_map_gameplay_offset_ = py::int_(constants.attr("FIGHTMGR_MAP_GAMEPLAY_OFFSET"));
            fightmgr_map_customtrap_offset_ = py::int_(constants.attr("FIGHTMGR_MAP_CUSTOMTRAP_OFFSET"));
            guard_seed_window_s_ = py::float_(constants.attr("GUARD_SEED_WINDOW_SECS"));
            guard_seed_max_ = py::int_(constants.attr("GUARD_SEED_MAX"));
            guard_flee_min_speed_ = py::float_(constants.attr("GUARD_FLEE_MIN_SPEED"));
            guard_min_survive_s_ = py::float_(constants.attr("GUARD_MIN_SURVIVE_SECS"));
        } catch (const py::error_already_set&) {
            fightmgr_map_monster_offset_ = 0;
            fightmgr_map_interactive_offset_ = 0;
            fightmgr_map_gameplay_offset_ = 0;
            fightmgr_map_customtrap_offset_ = 0;
            guard_seed_window_s_ = 4.0;
            guard_seed_max_ = 3;
            guard_flee_min_speed_ = 120.0;
            guard_min_survive_s_ = 1.5;
        }

        try {
            py::module_ scanner_mod = py::module_::import("src.core.scanner");
            py::object scanner_cls = scanner_mod.attr("UE4Scanner");
            carjack_pos_tolerance_ = py::float_(scanner_cls.attr("_CARJACK_POS_TOLERANCE"));
        } catch (const py::error_already_set&) {
            carjack_pos_tolerance_ = 200.0;
        }
    }

    bool memory_is_ready() const {
        return !memory_.is_none() && py::hasattr(memory_, "read_value");
    }

    static bool is_valid_ptr(std::uint64_t p) {
        return p >= 0x10000ull && p <= 0x7FFFFFFFFFFFull;
    }

    std::uint64_t read_ulong(std::uint64_t addr) const {
        if (!is_valid_ptr(addr) || !memory_is_ready()) {
            return 0;
        }
        try {
            py::object v = memory_.attr("read_value")(addr, "ulong");
            if (v.is_none()) {
                return 0;
            }
            return py::int_(v);
        } catch (const py::error_already_set&) {
            return 0;
        }
    }

    int read_int(std::uint64_t addr) const {
        if (!is_valid_ptr(addr) || !memory_is_ready()) {
            return 0;
        }
        try {
            py::object v = memory_.attr("read_value")(addr, "int");
            if (v.is_none()) {
                return 0;
            }
            return py::int_(v);
        } catch (const py::error_already_set&) {
            return 0;
        }
    }

    int read_byte(std::uint64_t addr) const {
        if (!is_valid_ptr(addr) || !memory_is_ready()) {
            return 0;
        }
        try {
            py::object v = memory_.attr("read_value")(addr, "byte");
            if (v.is_none()) {
                return 0;
            }
            return py::int_(v);
        } catch (const py::error_already_set&) {
            return 0;
        }
    }

    double read_float(std::uint64_t addr) const {
        if (!is_valid_ptr(addr) || !memory_is_ready()) {
            return 0.0;
        }
        try {
            py::object v = memory_.attr("read_value")(addr, "float");
            if (v.is_none()) {
                return 0.0;
            }
            return py::float_(v);
        } catch (const py::error_already_set&) {
            return 0.0;
        }
    }

    bool read_actor_position(std::uint64_t actor, double& x, double& y, double& z) const {
        // Shared UE4 actor transform chain in this project:
        // actor +0x130 (RootComponent) -> +0x124 (RelativeLocation FVector)
        const std::uint64_t root = read_ulong(actor + 0x130ull);
        if (!is_valid_ptr(root)) {
            return false;
        }
        x = read_float(root + 0x124ull);
        y = read_float(root + 0x128ull);
        z = read_float(root + 0x12Cull);
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) {
            return false;
        }
        return true;
    }

    std::uint64_t get_fightmgr_ptr_cached() {
        try {
            py::object p = call_backend0("get_fightmgr_ptr");
            if (p.is_none()) {
                return 0;
            }
            return py::int_(p);
        } catch (const py::error_already_set&) {
            return 0;
        }
    }

    std::uint64_t get_gworld_ptr_cached() {
        try {
            py::object p = call_backend0("get_gworld_ptr");
            if (p.is_none()) {
                return 0;
            }
            return py::int_(p);
        } catch (const py::error_already_set&) {
            return 0;
        }
    }

    std::vector<RawEvent> read_tmap_events(std::uint64_t fightmgr, int map_offset) {
        std::vector<RawEvent> out;
        if (!is_valid_ptr(fightmgr) || map_offset <= 0) {
            return out;
        }

        const std::uint64_t tmap_addr = fightmgr + static_cast<std::uint64_t>(map_offset);
        const std::uint64_t data_ptr = read_ulong(tmap_addr + 0x0);
        const int array_num = read_int(tmap_addr + 0x8);
        if (!is_valid_ptr(data_ptr) || array_num <= 0 || array_num > 50000) {
            return out;
        }

        const int n = std::min(array_num, 50000);
        out.reserve(static_cast<std::size_t>(n));
        for (int i = 0; i < n; ++i) {
            const std::uint64_t elem = data_ptr + static_cast<std::uint64_t>(i) * 0x18ull;
            const int key = read_int(elem + 0x0);
            const std::uint64_t ent = read_ulong(elem + 0x8);
            if (!is_valid_ptr(ent)) {
                continue;
            }

            RawEvent ev;
            ev.address = ent;
            ev.cfg_id = key;
            ev.wave_counter = read_int(ent + 0x618ull);
            ev.spawn_index = read_int(ent + 0x714ull);
            ev.bvalid = read_byte(ent + 0x720ull);
            read_actor_position(ent, ev.x, ev.y, ev.z);
            out.push_back(ev);
        }

        return out;
    }

    static double now_seconds() {
        using namespace std::chrono;
        return duration<double>(steady_clock::now().time_since_epoch()).count();
    }

    static bool valid_world_xy(double x, double y) {
        if (!std::isfinite(x) || !std::isfinite(y)) {
            return false;
        }
        return std::fabs(x) <= 120000.0 && std::fabs(y) <= 120000.0;
    }

    py::dict _empty_overlay_snapshot() const {
        py::dict d;
        d["event_markers"] = py::list();
        d["guard_markers"] = py::list();
        d["entity_markers"] = py::list();
        d["nav_collision_markers"] = py::list();
        d["dropped_event_markers"] = py::int_(0);
        d["dropped_guard_markers"] = py::int_(0);
        d["updated_at"] = py::float_(0.0);
        return d;
    }

    py::dict _build_overlay_snapshot() {
        py::list event_markers;
        py::list guard_markers;
        py::list entity_markers;
        py::list nav_collision_markers;
        int dropped_event_markers = 0;
        int dropped_guard_markers = 0;

        // Typed events -> overlay event markers.
        try {
            py::list events = py::list(get_typed_events());
            for (auto item : events) {
                py::object e = py::reinterpret_borrow<py::object>(item);
                py::tuple pos = attr_tuple3(e, "position");
                if (pos.size() < 2) {
                    continue;
                }
                const double x = py::float_(pos[0]);
                const double y = py::float_(pos[1]);
                if (std::fabs(x) <= 1.0 && std::fabs(y) <= 1.0) {
                    continue;
                }
                if (!valid_world_xy(x, y)) {
                    dropped_event_markers += 1;
                    continue;
                }
                py::dict m;
                m["x"] = py::float_(x);
                m["y"] = py::float_(y);
                m["type"] = py::str(py::str(py::getattr(e, "event_type", py::str("Unknown"))));
                m["wave"] = py::int_(attr_i(e, "wave_counter", -1));
                m["guards"] = py::int_(-1);
                m["guard_classes"] = py::str("");
                m["is_target"] = py::bool_(py::bool_(py::getattr(e, "is_target_event", py::bool_(false))));
                event_markers.append(m);
            }
        } catch (const py::error_already_set&) {
            event_markers = py::list();
            dropped_event_markers = 0;
        }

        // Carjack guard markers.
        try {
            py::list guards = py::list(get_carjack_guard_positions());
            for (auto item : guards) {
                py::dict g;
                if (py::isinstance<py::dict>(item)) {
                    g = py::reinterpret_borrow<py::dict>(item);
                } else {
                    continue;
                }
                const double x = py::float_(g.contains("x") ? g["x"] : py::float_(0.0));
                const double y = py::float_(g.contains("y") ? g["y"] : py::float_(0.0));
                if (!valid_world_xy(x, y)) {
                    dropped_guard_markers += 1;
                    continue;
                }
                py::dict m;
                m["x"] = py::float_(x);
                m["y"] = py::float_(y);
                m["abp"] = g.contains("abp") ? g["abp"] : py::str("");
                m["score"] = py::float_(0.0);
                m["dist_truck"] = g.contains("dist_truck") ? g["dist_truck"] : py::float_(-1.0);
                guard_markers.append(m);
            }
        } catch (const py::error_already_set&) {
            guard_markers = py::list();
            dropped_guard_markers = 0;
        }

        // Keep entities lightweight and bounded.
        try {
            py::list monsters = py::list(get_monster_entities());
            int emitted = 0;
            for (auto item : monsters) {
                if (emitted >= 120) {
                    break;
                }
                py::object m = py::reinterpret_borrow<py::object>(item);
                if (attr_i(m, "bvalid", 0) == 0) {
                    continue;
                }
                py::tuple pos = attr_tuple3(m, "position");
                if (pos.size() < 2) {
                    continue;
                }
                const double x = py::float_(pos[0]);
                const double y = py::float_(pos[1]);
                if (!valid_world_xy(x, y)) {
                    continue;
                }
                py::dict em;
                em["x"] = py::float_(x);
                em["y"] = py::float_(y);
                py::object cls = py::getattr(m, "class_name", py::str(""));
                em["name"] = py::str(py::str(cls).cast<std::string>().empty() ? "EMonster" : py::str(cls));
                entity_markers.append(em);
                emitted += 1;
            }
        } catch (const py::error_already_set&) {
            entity_markers = py::list();
        }

        // Nav-collision markers come from scanner-native accessor.
        try {
            py::list raw = py::list(get_nav_collision_markers());
            for (auto item : raw) {
                nav_collision_markers.append(item);
            }
        } catch (const py::error_already_set&) {
            nav_collision_markers = py::list();
        }

        py::dict out;
        out["event_markers"] = event_markers;
        out["guard_markers"] = guard_markers;
        out["entity_markers"] = entity_markers;
        out["nav_collision_markers"] = nav_collision_markers;
        out["dropped_event_markers"] = py::int_(dropped_event_markers);
        out["dropped_guard_markers"] = py::int_(dropped_guard_markers);
        out["updated_at"] = py::float_(now_seconds());
        return out;
    }

    static std::uint64_t attr_u64(const py::object& obj, const char* name, std::uint64_t def) {
        try {
            if (!py::hasattr(obj, name)) {
                return def;
            }
            py::object v = obj.attr(name);
            if (v.is_none()) {
                return def;
            }
            return py::int_(v);
        } catch (const py::error_already_set&) {
            return def;
        }
    }

    static int attr_i(const py::object& obj, const char* name, int def) {
        try {
            if (!py::hasattr(obj, name)) {
                return def;
            }
            py::object v = obj.attr(name);
            if (v.is_none()) {
                return def;
            }
            return py::int_(v);
        } catch (const py::error_already_set&) {
            return def;
        }
    }

    static py::tuple attr_tuple3(const py::object& obj, const char* name) {
        try {
            if (!py::hasattr(obj, name)) {
                return py::tuple();
            }
            py::object v = obj.attr(name);
            if (v.is_none()) {
                return py::tuple();
            }
            return py::tuple(v);
        } catch (const py::error_already_set&) {
            return py::tuple();
        }
    }

    static double map_get(const std::unordered_map<std::uint64_t, double>& m,
                          std::uint64_t k,
                          double def) {
        auto it = m.find(k);
        if (it == m.end()) {
            return def;
        }
        return it->second;
    }

    py::object call_backend0(const char* method) {
        if (backend_scanner_.is_none()) {
            throw py::attribute_error(std::string("backend scanner missing method: ") + method);
        }
        if (!py::hasattr(backend_scanner_, method)) {
            throw py::attribute_error(std::string("backend scanner missing method: ") + method);
        }
        return backend_scanner_.attr(method)();
    }

    template <typename... Args>
    py::object call_backend0(const char* method, Args&&... args) {
        if (backend_scanner_.is_none()) {
            throw py::attribute_error(std::string("backend scanner missing method: ") + method);
        }
        if (!py::hasattr(backend_scanner_, method)) {
            throw py::attribute_error(std::string("backend scanner missing method: ") + method);
        }
        return backend_scanner_.attr(method)(std::forward<Args>(args)...);
    }

    py::object read_chain_value(const char* chain_name) {
        if (addresses_.is_none() || memory_.is_none()) {
            return py::none();
        }
        if (!py::hasattr(addresses_, "get_chain") || !py::hasattr(memory_, "read_pointer_chain")) {
            return py::none();
        }
        py::object chain = addresses_.attr("get_chain")(chain_name);
        if (chain.is_none()) {
            return py::none();
        }
        return memory_.attr("read_pointer_chain")(chain);
    }

    py::object memory_;
    py::object addresses_;
    py::object progress_callback_;
    py::object backend_scanner_;
    py::object cached_gworld_static_ = py::none();
    int fightmgr_map_monster_offset_ = 0;
    int fightmgr_map_interactive_offset_ = 0;
    int fightmgr_map_gameplay_offset_ = 0;
    int fightmgr_map_customtrap_offset_ = 0;
    double guard_seed_window_s_ = 4.0;
    int guard_seed_max_ = 3;
    double guard_flee_min_speed_ = 120.0;
    double guard_min_survive_s_ = 1.5;
    double carjack_pos_tolerance_ = 200.0;
    std::string last_real_zone_name_;
    bool carjack_truck_valid_ = false;
    double carjack_truck_x_ = 0.0;
    double carjack_truck_y_ = 0.0;
    std::uint64_t carjack_vehicle_addr_ = 0;
    double carjack_active_since_ = 0.0;
    double carjack_active_until_ = 0.0;
    int guard_seed_count_ = 0;
    std::unordered_set<std::uint64_t> guard_seed_addrs_;
    std::unordered_map<std::uint64_t, std::deque<PosSample>> entity_pos_history_;
    std::unordered_map<std::uint64_t, double> entity_first_seen_t_;
    mutable std::mutex overlay_mutex_;
    std::thread overlay_thread_;
    std::atomic<bool> overlay_stop_{false};
    std::atomic<bool> overlay_worker_alive_{false};
    py::dict overlay_snapshot_;
    double overlay_interval_s_ = 0.20;
    bool cancelled_ = false;
};

static py::dict get_runtime_info() {
    py::dict d;
    d["module"] = "tli_native";
    d["status"] = "native_scanner_overlay";
    d["scanner"] = "native_scanner_object";
    d["overlay_worker"] = "implemented";
    d["phase_b"] = "slice_2_overlay";
    return d;
}

static py::object create_scanner(py::object memory,
                                 py::object addresses,
                                 py::object progress_callback,
                                 py::object backend_scanner = py::none()) {
    auto scanner = std::make_shared<NativeScanner>(
        std::move(memory),
        std::move(addresses),
        std::move(progress_callback),
        std::move(backend_scanner)
    );
    return py::cast(scanner);
}

PYBIND11_MODULE(tli_native, m) {
    m.doc() = "Torchlight Infinite native runtime module";

    py::class_<NativeScanner, std::shared_ptr<NativeScanner>>(m, "NativeScanner")
        .def("scan_dump_chain", &NativeScanner::scan_dump_chain, py::arg("use_cache") = true)
        .def("scan_fnamepool", &NativeScanner::scan_fnamepool, py::arg("module_base") = 0, py::arg("module_size") = 0)
        .def("scan_gobjects", &NativeScanner::scan_gobjects, py::arg("module_base") = 0, py::arg("module_size") = 0)
        .def("set_cached_gworld_static", &NativeScanner::set_cached_gworld_static)
        .def("clear_fightmgr_cache", &NativeScanner::clear_fightmgr_cache)
        .def("set_nav_collision_probe", &NativeScanner::set_nav_collision_probe, py::arg("enabled"), py::arg("interval_s") = 2.0)
        .def("check_chain_valid", &NativeScanner::check_chain_valid)
        .def("get_gworld_ptr", &NativeScanner::get_gworld_ptr)
        .def("read_player_xy", &NativeScanner::read_player_xy)
        .def("read_zone_name", &NativeScanner::read_zone_name)
        .def("read_real_zone_name", &NativeScanner::read_real_zone_name)
        .def("get_typed_events", &NativeScanner::get_typed_events)
        .def("get_monster_entities", &NativeScanner::get_monster_entities)
           .def("count_nearby_monsters", &NativeScanner::count_nearby_monsters,
               py::arg("x"), py::arg("y"), py::arg("radius") = 2500.0)
        .def("get_carjack_truck_position", &NativeScanner::get_carjack_truck_position)
        .def("get_carjack_guard_positions", &NativeScanner::get_carjack_guard_positions)
           .def("get_nearby_interactive_items", &NativeScanner::get_nearby_interactive_items,
               py::arg("x"), py::arg("y"), py::arg("radius") = 3000.0, py::arg("require_valid") = true)
        .def("scan_boss_room", &NativeScanner::scan_boss_room)
           .def("read_minimap_visited_positions", &NativeScanner::read_minimap_visited_positions,
               py::arg("raw_zone_name") = "")
        .def("get_nav_collision_markers", &NativeScanner::get_nav_collision_markers)
        .def("get_fightmgr_ptr", &NativeScanner::get_fightmgr_ptr)
        .def("find_object_by_name", &NativeScanner::find_object_by_name)
        .def("read_player_hp", &NativeScanner::read_player_hp)
        .def_property_readonly("fnamepool_addr", &NativeScanner::fnamepool_addr)
        .def_property_readonly("gobjects_addr", &NativeScanner::gobjects_addr)
           .def_property_readonly("_fnamepool_addr", &NativeScanner::_fnamepool_addr)
           .def_property_readonly("_gobjects_addr", &NativeScanner::_gobjects_addr)
           .def_property_readonly("_memory", &NativeScanner::_memory)
           .def_property_readonly("_scanner", &NativeScanner::_scanner)
        .def("set_fnamepool_addr", &NativeScanner::set_fnamepool_addr)
           .def("_read_truck_guard_roster", &NativeScanner::_read_truck_guard_roster,
               py::arg("truck_addr"), py::arg("fnamepool"))
        .def("start_overlay_worker", &NativeScanner::start_overlay_worker, py::arg("interval_s") = 0.20)
        .def("stop_overlay_worker", &NativeScanner::stop_overlay_worker)
        .def("get_overlay_snapshot", &NativeScanner::get_overlay_snapshot)
        .def_property_readonly("overlay_worker_alive", &NativeScanner::overlay_worker_alive)
        .def("cancel", &NativeScanner::cancel);

    m.def("get_runtime_info", &get_runtime_info, "Return native runtime metadata");
    m.def(
        "create_scanner",
        &create_scanner,
        py::arg("memory"),
        py::arg("addresses"),
        py::arg("progress_callback"),
        py::arg("backend_scanner") = py::none(),
        "Create native scanner instance"
    );
}
