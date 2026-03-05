#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <string>
#include <utility>

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

    py::object read_zone_name() { return call_backend0("read_zone_name"); }
    py::object read_real_zone_name() { return call_backend0("read_real_zone_name"); }
    py::object get_typed_events() { return call_backend0("get_typed_events"); }
    py::object get_monster_entities() { return call_backend0("get_monster_entities"); }

    py::object count_nearby_monsters(py::object x, py::object y, py::object radius) {
        return call_backend0("count_nearby_monsters", std::move(x), std::move(y), std::move(radius));
    }

    py::object get_carjack_truck_position() { return call_backend0("get_carjack_truck_position"); }
    py::object get_carjack_guard_positions() { return call_backend0("get_carjack_guard_positions"); }

    py::object get_nearby_interactive_items(py::object x, py::object y, py::object radius) {
        return call_backend0("get_nearby_interactive_items", std::move(x), std::move(y), std::move(radius));
    }

    py::object scan_boss_room() { return call_backend0("scan_boss_room"); }
    py::object read_minimap_visited_positions() { return call_backend0("read_minimap_visited_positions"); }
    py::object get_nav_collision_markers() { return call_backend0("get_nav_collision_markers"); }
    py::object get_fightmgr_ptr() { return call_backend0("get_fightmgr_ptr"); }

    py::object find_object_by_name(py::object name) {
        return call_backend0("find_object_by_name", std::move(name));
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

    py::object set_fnamepool_addr(py::object value) {
        return call_backend0("set_fnamepool_addr", std::move(value));
    }

    py::object _read_truck_guard_roster() { return call_backend0("_read_truck_guard_roster"); }

    void cancel() {
        cancelled_ = true;
        call_backend0("cancel");
    }

private:
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
    bool cancelled_ = false;
};

static py::dict get_runtime_info() {
    py::dict d;
    d["module"] = "tli_native";
    d["status"] = "native_scanner_slice";
    d["scanner"] = "native_scanner_object";
    d["overlay_worker"] = "not_implemented";
    d["phase_b"] = "slice_1";
    return d;
}

static py::object create_scanner(py::object memory,
                                 py::object addresses,
                                 py::object progress_callback,
                                 py::object backend_scanner = py::none()) {
    return py::cast(NativeScanner(
        std::move(memory),
        std::move(addresses),
        std::move(progress_callback),
        std::move(backend_scanner)
    ));
}

PYBIND11_MODULE(tli_native, m) {
    m.doc() = "Torchlight Infinite native runtime module";

    py::class_<NativeScanner>(m, "NativeScanner")
        .def("scan_dump_chain", &NativeScanner::scan_dump_chain, py::arg("use_cache") = true)
        .def("scan_fnamepool", &NativeScanner::scan_fnamepool, py::arg("module_base") = 0, py::arg("module_size") = 0)
        .def("scan_gobjects", &NativeScanner::scan_gobjects, py::arg("module_base") = 0, py::arg("module_size") = 0)
        .def("set_cached_gworld_static", &NativeScanner::set_cached_gworld_static)
        .def("clear_fightmgr_cache", &NativeScanner::clear_fightmgr_cache)
        .def("check_chain_valid", &NativeScanner::check_chain_valid)
        .def("get_gworld_ptr", &NativeScanner::get_gworld_ptr)
        .def("read_player_xy", &NativeScanner::read_player_xy)
        .def("read_zone_name", &NativeScanner::read_zone_name)
        .def("read_real_zone_name", &NativeScanner::read_real_zone_name)
        .def("get_typed_events", &NativeScanner::get_typed_events)
        .def("get_monster_entities", &NativeScanner::get_monster_entities)
        .def("count_nearby_monsters", &NativeScanner::count_nearby_monsters)
        .def("get_carjack_truck_position", &NativeScanner::get_carjack_truck_position)
        .def("get_carjack_guard_positions", &NativeScanner::get_carjack_guard_positions)
        .def("get_nearby_interactive_items", &NativeScanner::get_nearby_interactive_items)
        .def("scan_boss_room", &NativeScanner::scan_boss_room)
        .def("read_minimap_visited_positions", &NativeScanner::read_minimap_visited_positions)
        .def("get_nav_collision_markers", &NativeScanner::get_nav_collision_markers)
        .def("get_fightmgr_ptr", &NativeScanner::get_fightmgr_ptr)
        .def("find_object_by_name", &NativeScanner::find_object_by_name)
        .def("read_player_hp", &NativeScanner::read_player_hp)
        .def_property_readonly("fnamepool_addr", &NativeScanner::fnamepool_addr)
        .def_property_readonly("gobjects_addr", &NativeScanner::gobjects_addr)
        .def("set_fnamepool_addr", &NativeScanner::set_fnamepool_addr)
        .def("_read_truck_guard_roster", &NativeScanner::_read_truck_guard_roster)
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
