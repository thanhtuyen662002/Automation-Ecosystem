import { useState } from "react";
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

type TaskDef = {
  id: number;
  task_key: string;
  task_type: string;
  payload: Record<string, any>;
  depends_on: string[];
};

export function CreateJobPage() {
  const [workflowName, setWorkflowName] = useState("");
  const [tasks, setTasks] = useState<TaskDef[]>([
    { id: Date.now(), task_key: "task_1", task_type: "generate_caption_ai", payload: {}, depends_on: [] }
  ]);
  const [errors, setErrors] = useState<string[]>([]);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const accountsQuery = useQuery({
    queryKey: ["accounts"],
    queryFn: () => fetch("/api/v1/accounts").then(res => res.json())
  });

  const schemasQuery = useQuery({
    queryKey: ["task_schemas"],
    queryFn: () => fetch("/api/v1/tasks/schemas").then(res => res.json())
  });

  const schemas = schemasQuery.data || {};

  const validate = () => {
    const errs: string[] = [];
    const keys = new Set();
    
    tasks.forEach((t, i) => {
      if (!t.task_key) errs.push(`Task ${i+1}: Missing task_key`);
      if (keys.has(t.task_key)) errs.push(`Task ${i+1}: Duplicate task_key '${t.task_key}'`);
      keys.add(t.task_key);
      
      const schema = schemas[t.task_type];
      if (schema) {
        schema.required?.forEach((req: string) => {
          if (!t.payload[req] && t.payload[req] !== 0 && t.payload[req] !== false) {
            errs.push(`Task '${t.task_key}' missing required field: ${req}`);
          }
        });
      }
    });

    tasks.forEach(t => {
      t.depends_on.forEach(dep => {
        if (!keys.has(dep)) errs.push(`Task '${t.task_key}' depends on unknown task '${dep}'`);
      });
      // Validate dynamic payload refs
      Object.entries(t.payload).forEach(([k, v]) => {
        if (v && typeof v === 'object' && v.from_task && v.field) {
          if (!keys.has(v.from_task)) errs.push(`Task '${t.task_key}' references unknown task '${v.from_task}' in field '${k}'`);
          const sourceTask = tasks.find(x => x.task_key === v.from_task);
          if (sourceTask) {
            const sourceSchema = schemas[sourceTask.task_type];
            if (sourceSchema && !sourceSchema.output?.includes(v.field)) {
              errs.push(`Task '${t.task_key}' references invalid field '${v.field}' from task '${v.from_task}'`);
            }
          }
        }
      });
    });

    setErrors(errs);
    return errs.length === 0;
  };

  const addTask = () => {
    setTasks([...tasks, { id: Date.now(), task_key: `task_${tasks.length + 1}`, task_type: Object.keys(schemas)[0] || "ai", payload: {}, depends_on: [] }]);
  };

  const updateTask = (id: number, field: keyof TaskDef, value: any) => {
    setTasks(tasks.map(t => (t.id === id ? { ...t, [field]: value } : t)));
  };

  const updatePayload = (id: number, key: string, value: any) => {
    setTasks(tasks.map(t => {
      if (t.id !== id) return t;
      return { ...t, payload: { ...t.payload, [key]: value } };
    }));
  };

  const removeTask = (id: number) => {
    setTasks(tasks.filter(t => t.id !== id));
  };

  const createJob = useMutation({
    mutationFn: () => {
      const formattedTasks = tasks.map(t => ({
        task_key: t.task_key,
        task_type: t.task_type,
        payload: t.payload,
        depends_on: t.depends_on
      }));

      return fetch("/api/v1/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow_name: workflowName, tasks: formattedTasks })
      }).then(async res => {
        if (!res.ok) throw new Error((await res.json()).message || "Failed to create");
        return res.json();
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      navigate("/jobs");
    },
    onError: (err: any) => {
      setErrors([err.message]);
    }
  });

  const handleCreate = () => {
    if (validate()) createJob.mutate();
  };

  return (
    <div className="p-4 space-y-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold">Workflow Builder</h1>
      
      {errors.length > 0 && (
        <div className="bg-red-50 text-red-700 p-4 rounded border border-red-200">
          <h3 className="font-bold mb-2">Validation Errors:</h3>
          <ul className="list-disc pl-5">
            {errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}

      <div className="space-y-2">
        <label className="font-semibold block">Workflow Name</label>
        <input 
          className="border border-gray-300 rounded p-2 w-full max-w-md" 
          placeholder="e.g. Viral Video Pipeline"
          value={workflowName} 
          onChange={e => setWorkflowName(e.target.value)} 
        />
      </div>

      <div className="space-y-6">
        {tasks.map((task, index) => {
          const otherTasks = tasks.filter(t => t.task_key !== task.task_key && t.task_key.trim() !== "");
          const schema = schemas[task.task_type] || { required: [], optional: [], output: [] };
          const payloadFields = [...(schema.required || []), ...(schema.optional || [])];

          return (
            <div key={task.id} className="border border-gray-200 p-5 rounded-lg space-y-4 bg-white shadow-sm">
              <div className="flex justify-between items-center border-b pb-2">
                <h3 className="font-bold text-lg text-gray-800">Task: {task.task_key || `[Empty ${index+1}]`}</h3>
                {tasks.length > 1 && (
                  <button className="text-red-500 hover:bg-red-50 px-2 py-1 rounded text-sm font-medium" onClick={() => removeTask(task.id)}>Remove</button>
                )}
              </div>
              
              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="block text-sm font-medium mb-1 text-gray-700">Task Key</label>
                  <input className="border border-gray-300 p-2 rounded w-full" value={task.task_key} onChange={e => updateTask(task.id, "task_key", e.target.value)} />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1 text-gray-700">Task Type</label>
                  <select className="border border-gray-300 p-2 rounded w-full" value={task.task_type} onChange={e => {
                    updateTask(task.id, "task_type", e.target.value);
                    updateTask(task.id, "payload", {});
                  }}>
                    {Object.keys(schemas).map(k => <option key={k} value={k}>{k}</option>)}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="block text-sm font-medium mb-1 text-gray-700">Dependencies (Comma separated)</label>
                  <input 
                    className="border border-gray-300 p-2 rounded w-full" 
                    placeholder="task_1, task_2"
                    value={task.depends_on.join(", ")} 
                    onChange={e => updateTask(task.id, "depends_on", e.target.value.split(",").map(s => s.trim()).filter(Boolean))} 
                  />
                </div>
              </div>

              <div className="border-t pt-4 mt-4">
                <h4 className="font-semibold mb-3 text-gray-700">Payload Builder</h4>
                <div className="space-y-4">
                  {payloadFields.map(field => {
                    const isRequired = schema.required?.includes(field);
                    const val = task.payload[field];
                    const isDynamic = val && typeof val === 'object' && val.from_task;

                    return (
                      <div key={field} className="flex flex-col space-y-1 bg-gray-50 p-3 rounded border">
                        <div className="flex justify-between items-center">
                          <label className="text-sm font-medium text-gray-800">
                            {field} {isRequired && <span className="text-red-500">*</span>}
                          </label>
                          <div className="text-xs text-gray-500">
                            <label className="flex items-center space-x-1 cursor-pointer">
                              <input type="checkbox" checked={!!isDynamic} onChange={(e) => {
                                if (e.target.checked) updatePayload(task.id, field, { from_task: "", field: "" });
                                else updatePayload(task.id, field, "");
                              }} />
                              <span>Dynamic Reference</span>
                            </label>
                          </div>
                        </div>

                        {field === "account_id" && !isDynamic ? (
                          <select className="border border-gray-300 p-2 rounded w-full text-sm" value={val || ""} onChange={e => updatePayload(task.id, field, e.target.value)}>
                            <option value="">Select account...</option>
                            {accountsQuery.data?.items?.filter((a: any) => task.task_type.includes(a.platform)).map((acc: any) => (
                              <option key={acc.id} value={acc.id}>{acc.platform} - {acc.account_handle}</option>
                            ))}
                          </select>
                        ) : isDynamic ? (
                          <div className="flex space-x-2">
                            <select 
                              className="border border-blue-300 bg-blue-50 p-2 rounded w-1/2 text-sm" 
                              value={val.from_task} 
                              onChange={e => {
                                updatePayload(task.id, field, { ...val, from_task: e.target.value, field: "" });
                                const newDepends = task.depends_on.includes(e.target.value) ? task.depends_on : [...task.depends_on, e.target.value];
                                updateTask(task.id, "depends_on", newDepends);
                              }}
                            >
                              <option value="">Select source task...</option>
                              {otherTasks.map(t => <option key={t.task_key} value={t.task_key}>{t.task_key}</option>)}
                            </select>
                            
                            <select 
                              className="border border-blue-300 bg-blue-50 p-2 rounded w-1/2 text-sm" 
                              value={val.field} 
                              onChange={e => updatePayload(task.id, field, { ...val, field: e.target.value })}
                              disabled={!val.from_task}
                            >
                              <option value="">Select output field...</option>
                              {(schemas[otherTasks.find(t => t.task_key === val.from_task)?.task_type || ""]?.output || []).map((out: string) => (
                                <option key={out} value={out}>{out}</option>
                              ))}
                            </select>
                          </div>
                        ) : (
                          <input 
                            className="border border-gray-300 p-2 rounded w-full text-sm" 
                            value={val || ""} 
                            onChange={e => updatePayload(task.id, field, e.target.value)} 
                            placeholder={`Enter ${field}...`}
                          />
                        )}
                      </div>
                    );
                  })}
                  {payloadFields.length === 0 && (
                    <p className="text-sm text-gray-500 italic">No fields required for this task type.</p>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex space-x-4 pt-4 border-t">
        <button className="border border-gray-300 hover:bg-gray-50 font-medium px-4 py-2 rounded shadow-sm" onClick={addTask}>
          + Add Task
        </button>
        <button 
          className="bg-blue-600 hover:bg-blue-700 text-white font-medium px-6 py-2 rounded shadow-sm disabled:opacity-50" 
          onClick={handleCreate}
          disabled={!workflowName || tasks.length === 0 || createJob.isPending}
        >
          {createJob.isPending ? "Creating..." : "Save & Create Workflow"}
        </button>
      </div>
    </div>
  );
}
